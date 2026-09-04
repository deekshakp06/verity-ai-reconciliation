"""
matcher.py
----------
The core reconciliation logic. Deliberately rule-based and explainable (not a
black-box model) — for a finance-ops decision, "why did you match/not match
these two records" needs a clear, auditable answer every time.

Matching strategy:
  1. Try to match on a normalized reference ID (fuzzy string match, since
     internal IDs and bank references are formatted completely differently).
  2. For each candidate pair, score confidence based on:
       - amount difference (small tolerance = fee; large = real mismatch)
       - date gap (settlement normally lags a few days; too long = suspicious)
  3. Only auto-match above a confidence threshold. Anything below it becomes
     an EXCEPTION with a specific reason — never force-matched.

This module returns three things: matched pairs, exceptions, and a full
decision log (used for the audit trail).
"""

from dataclasses import dataclass, field
from datetime import datetime
import time
import pandas as pd
from rapidfuzz import fuzz

from config import (
    AUTO_MATCH_THRESHOLD,
    AMOUNT_TOLERANCE_PCT,
    MAX_REASONABLE_DELAY_DAYS,
    MIN_ID_SIMILARITY_TO_CONSIDER,
)

# How strongly a settlement's reference must match THIS payment's own
# reference before it counts toward duplicate-signature detection. Deliberately
# much stricter than MIN_ID_SIMILARITY_TO_CONSIDER (which just gates whether a
# row is worth scoring at all) — duplicate detection needs near-certainty this
# candidate genuinely belongs to this payment, not just a plausible one.
STRONG_ID_SIMILARITY_FOR_DUPLICATE_CHECK = 90
from logger import get_logger

logger = get_logger(__name__)


@dataclass
class Decision:
    payment_id: str
    settlement_ref: str | None
    status: str          # "matched" | "exception"
    confidence: float
    reason: str
    amount_a: float | None = None
    amount_b: float | None = None
    date_a: str | None = None
    date_b: str | None = None


def _validate_input(df: pd.DataFrame, label: str, allow_empty: bool = False):
    """Fail fast with a clear message instead of crashing deep inside matching logic."""
    if df is None:
        raise ValueError(f"{label} dataframe is missing (None) — nothing to reconcile.")

    required = {"id", "amount", "date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} is missing required canonical columns: {missing}")

    if df.empty:
        if allow_empty:
            return  # e.g. no settlements have arrived yet — valid, not an error
        raise ValueError(f"{label} dataframe is empty — nothing to reconcile.")

    if df["id"].isnull().any():
        raise ValueError(f"{label} contains rows with a missing id — cannot match those safely.")

    bad_amounts = df[~df["amount"].apply(lambda x: isinstance(x, (int, float)) and x == x)]
    if not bad_amounts.empty:
        raise ValueError(
            f"{label} has {len(bad_amounts)} row(s) with a non-numeric or missing amount."
        )

    try:
        pd.to_datetime(df["date"])
    except Exception as e:
        raise ValueError(f"{label} has unparseable date values: {e}")


def _date_gap_days(d1, d2):
    return abs((pd.to_datetime(d2) - pd.to_datetime(d1)).days)


def _score_pair(payment_row, settlement_row, id_similarity: float, has_cross_reference_field: bool) -> tuple[float, str]:
    """
    Returns (confidence 0-100, reason string) for a candidate pair.

    Confidence blends TWO signals — but ONLY when it's safe to do so:
      - how well amount + date line up (always used)
      - how strongly the reference/ID text matches (only used when the
        payment data actually carries a genuine cross-reference field to the
        settlement side, e.g. "internal_ref"). Without such a field, a
        payment's own ID and a settlement's own ID are just two independently
        formatted schemes — comparing them textually is noise, not signal,
        and would incorrectly punish correct matches whose ID formats simply
        don't resemble each other by coincidence.

    Why blend at all: amount alone can coincidentally look like a near-perfect
    match between two completely UNRELATED transactions (e.g. two payments of
    ₹5,000.00 and ₹5,000.05 happening around the same time). Reference
    similarity is the signal that actually distinguishes them, when available.
    """
    amt_a, amt_b = payment_row["amount"], settlement_row["amount"]
    pct_diff = abs(amt_a - amt_b) / amt_a * 100 if amt_a else 100

    gap = _date_gap_days(payment_row["date"], settlement_row["date"])

    if pct_diff <= AMOUNT_TOLERANCE_PCT and gap <= MAX_REASONABLE_DELAY_DAYS:
        # still graded within the "clean" band so the very best clean match wins
        base_confidence = 100 - (pct_diff * 2) - (gap * 0.5)
        base_confidence = max(base_confidence, 90.0)
        reason = (
            "Amount and date align within normal tolerance — the difference is "
            "consistent with a standard payment gateway fee, not an error."
        )
    elif pct_diff > AMOUNT_TOLERANCE_PCT and gap > MAX_REASONABLE_DELAY_DAYS:
        # both amount AND date are off — worse than either issue alone
        base_confidence = max(10.0, 50 - pct_diff * 0.3 - gap * 0.5)
        reason = (
            f"Amount mismatch of {abs(amt_a - amt_b):.2f} ({pct_diff:.1f}% difference) "
            f"AND settlement is {gap} days late — multiple discrepancies, needs human review."
        )
    elif pct_diff > AMOUNT_TOLERANCE_PCT:
        # graded so a ₹5 mismatch scores far higher than a ₹5,000 mismatch
        base_confidence = max(15.0, 70 - pct_diff * 0.5)
        reason = (
            f"Amount mismatch of {abs(amt_a - amt_b):.2f} "
            f"({pct_diff:.1f}% difference) — possibly a processing fee, "
            f"needs human confirmation."
        )
    elif gap > MAX_REASONABLE_DELAY_DAYS:
        base_confidence = max(20.0, 65 - gap * 1.5)
        reason = (
            f"Settlement date is {gap} days after payment date — longer than "
            f"the normal settlement window, worth checking for a delayed or "
            f"stuck payment."
        )
    else:
        base_confidence = 40.0
        reason = "Multiple minor discrepancies found."

    # Blend: 75% amount/date fit, 25% reference-text similarity — but ONLY
    # when a genuine cross-reference field exists. Otherwise, ID similarity
    # is unreliable noise and the original amount/date-only score is used.
    if has_cross_reference_field:
        blended = round(base_confidence * 0.75 + id_similarity * 0.25, 1)
        if id_similarity < 40 and base_confidence >= 90:
            reason += (
                f" Note: reference codes don't look related (similarity {id_similarity:.0f}%) "
                f"despite the amount/date match — confidence reduced accordingly."
            )
    else:
        blended = round(base_confidence, 1)

    return blended, reason


def reconcile(payments: pd.DataFrame, settlements: pd.DataFrame) -> dict:
    """
    payments / settlements: dataframes already normalized to columns
    [id, amount, date, ...] via schema_mapper.apply_mapping()

    Returns: {"matched": [...], "exceptions": [...], "decisions": [Decision, ...]}

    Raises ValueError on malformed input rather than failing partway through
    with a confusing stack trace.
    """
    # Payments must be non-empty (there's nothing to reconcile against otherwise).
    # Settlements MAY legitimately be empty — e.g. nothing has settled yet —
    # in which case every payment correctly becomes an exception below.
    _validate_input(payments, "payments", allow_empty=False)
    _validate_input(settlements, "settlements", allow_empty=True)

    logger.info(
        "Starting reconciliation: %d payment records vs %d settlement records",
        len(payments), len(settlements),
    )
    _start_time = time.perf_counter()

    # Whether payments carry a genuine cross-reference field pointing at the
    # settlement side (like the demo data's "internal_ref"). Without one, a
    # payment's own ID and a settlement's own ID are just two independently
    # formatted schemes — comparing them textually is noise, not signal, so
    # reference similarity should NOT influence scoring in that case.
    has_cross_reference_field = "internal_ref" in payments.columns

    decisions: list[Decision] = []
    matched_settlement_indices = set()

    for _, prow in payments.iterrows():
        best_idx, best_score, best_reason = None, -1, ""
        # Tracks settlement candidates by their own (amount, date) signature —
        # genuine duplicate settlement entries are literal copies of EACH
        # OTHER (not of the payment, which naturally differs by the normal
        # settlement lag). Two candidates sharing an identical signature is
        # the real duplicate signal.
        signature_counts: dict[tuple, int] = {}

        for sidx, srow in settlements.iterrows():
            if sidx in matched_settlement_indices:
                continue
            # only compare candidates whose reference strings are plausibly related
            # (fuzzy match on the raw id strings) OR whose amounts are reasonably close
            id_similarity = fuzz.partial_ratio(str(prow.get("internal_ref", prow["id"])), str(srow["id"]))
            amt_close = abs(prow["amount"] - srow["amount"]) / max(prow["amount"], 1) < 0.05

            if id_similarity < MIN_ID_SIMILARITY_TO_CONSIDER and not amt_close:
                continue  # not even a plausible candidate, skip

            # Only count a settlement toward THIS payment's duplicate-signature
            # check if it's strongly reference-matched to this specific payment
            # (near-exact id similarity) — the loose amount-only candidate
            # filter above is too wide and can otherwise pull in a genuinely
            # unrelated payment's own duplicate settlement rows purely by
            # amount coincidence.
            if id_similarity >= STRONG_ID_SIMILARITY_FOR_DUPLICATE_CHECK:
                signature = (round(srow["amount"], 2), str(srow["date"]))
                signature_counts[signature] = signature_counts.get(signature, 0) + 1

            score, reason = _score_pair(prow, srow, id_similarity, has_cross_reference_field)
            if score > best_score:
                best_idx, best_score, best_reason = sidx, score, reason

        max_duplicate_signature_count = max(signature_counts.values()) if signature_counts else 0

        if best_idx is None:
            decisions.append(
                Decision(
                    payment_id=prow["id"],
                    settlement_ref=None,
                    status="exception",
                    confidence=0.0,
                    reason="No plausible matching settlement record found at all — "
                           "payment may not have settled yet, or settlement data is missing.",
                    amount_a=prow["amount"],
                    date_a=str(prow["date"]),
                )
            )
            continue

        srow = settlements.loc[best_idx]

        if max_duplicate_signature_count > 1:
            # Deliberately NOT auto-matched: multiple settlement records equally
            # plausibly belong to this one payment. Force-picking one would hide
            # a real risk (duplicate settlement / possible double payment).
            decisions.append(
                Decision(
                    payment_id=prow["id"],
                    settlement_ref=srow["id"],
                    status="exception",
                    confidence=best_score,
                    reason=f"Possible duplicate settlement — {max_duplicate_signature_count} "
                           f"unclaimed settlement records share an identical amount and "
                           f"date, so this payment's true match is ambiguous. Needs human "
                           f"review before picking one.",
                    amount_a=prow["amount"],
                    amount_b=srow["amount"],
                    date_a=str(prow["date"]),
                    date_b=str(srow["date"]),
                )
            )
            continue

        if best_score >= AUTO_MATCH_THRESHOLD:
            matched_settlement_indices.add(best_idx)
            decisions.append(
                Decision(
                    payment_id=prow["id"],
                    settlement_ref=srow["id"],
                    status="matched",
                    confidence=best_score,
                    reason=best_reason,
                    amount_a=prow["amount"],
                    amount_b=srow["amount"],
                    date_a=str(prow["date"]),
                    date_b=str(srow["date"]),
                )
            )
        else:
            # NOT force-matched — this is the deliberate "graceful refusal" behavior
            decisions.append(
                Decision(
                    payment_id=prow["id"],
                    settlement_ref=srow["id"],
                    status="exception",
                    confidence=best_score,
                    reason=best_reason,
                    amount_a=prow["amount"],
                    amount_b=srow["amount"],
                    date_a=str(prow["date"]),
                    date_b=str(srow["date"]),
                )
            )

    matched = [d for d in decisions if d.status == "matched"]
    exceptions = [d for d in decisions if d.status == "exception"]

    elapsed = time.perf_counter() - _start_time
    throughput = round(len(decisions) / elapsed, 1) if elapsed > 0 else len(decisions)

    logger.info(
        "Reconciliation complete: %d matched, %d exceptions, match_rate=%.2f%%, "
        "elapsed=%.3fs, throughput=%.1f records/sec",
        len(matched), len(exceptions),
        round(100 * len(matched) / len(decisions), 2) if decisions else 0.0,
        elapsed, throughput,
    )

    return {
        "decisions": decisions,
        "matched": matched,
        "exceptions": exceptions,
        "match_rate": round(100 * len(matched) / len(decisions), 2) if decisions else 0.0,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_per_sec": throughput,
    }
