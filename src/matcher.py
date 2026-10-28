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
  3. IDENTITY FIRST: when payments carry a cross-reference field, the
     reference code decides which settlement a payment is compared with. An
     exact (normalized) reference is looked up directly; fuzzy matching is only
     a fallback, and a pair whose references disagree is never auto-matched.
  4. Only auto-match above a confidence threshold. Anything below it becomes
     an EXCEPTION with a specific reason — never force-matched.

This module returns three things: matched pairs, exceptions, and a full
decision log (used for the audit trail).
"""

from dataclasses import dataclass, field
import os
import re
import time
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from money import paise
from config import (
    AUTO_MATCH_THRESHOLD,
    AMOUNT_TOLERANCE_PCT,
    MAX_REASONABLE_DELAY_DAYS,
    MAX_LATE_SETTLEMENT_DAYS,
    MIN_ID_SIMILARITY_TO_CONSIDER,
    REFERENCE_MATCH_MIN_SIMILARITY,
    FEE_CHECK_MIN_PAIRS,
    FEE_CONSISTENCY_MAX_MAD_PP,
    FEE_DEVIATION_PP,
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
    # Informational flags on a decision, e.g. ["late_settlement"]: matched,
    # but a human may still want to look at it.
    flags: list = field(default_factory=list)


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
    """Row-based wrapper kept for callers/tests; reconcile() uses _score_values directly."""
    return _score_values(payment_row["amount"], settlement_row["amount"],
                         _date_gap_days(payment_row["date"], settlement_row["date"]),
                         id_similarity, has_cross_reference_field)


def _score_values(amt_a, amt_b, gap, id_similarity: float, has_cross_reference_field: bool) -> tuple[float, str]:
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
    pct_diff = abs(amt_a - amt_b) / amt_a * 100 if amt_a else 100

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


_DAY = np.timedelta64(1, "D")
_MISSING_REFS = {"", "NAN", "NONE", "NULL", "NA"}
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _norm_ref(x) -> str:
    return _NON_ALNUM.sub("", str(x).upper())


def _strip_common_prefix(values: list[str]) -> list[str]:
    """Drop a prefix shared by the whole column (e.g. "REF"). Otherwise it makes
    unrelated codes look 60-80% similar and drowns out the part that actually
    identifies the transaction."""
    if not values:
        return values
    pre = os.path.commonprefix(values)
    n = len(pre) if 2 <= len(pre) < min(len(v) for v in values) else 0
    return [v[n:] for v in values]


def _scores(pa, da, sa, ds, idsim, has_cross):
    """Vectorized equivalent of _score_pair's number (verified identical)."""
    pct = np.full(sa.shape, 100.0) if not pa else np.abs(pa - sa) / pa * 100
    gap = np.abs(np.floor((ds - da) / _DAY))
    pct_bad, gap_bad = pct > AMOUNT_TOLERANCE_PCT, gap > MAX_REASONABLE_DELAY_DAYS
    base = np.select(
        [~pct_bad & ~gap_bad, pct_bad & gap_bad, pct_bad, gap_bad],
        [np.maximum(100 - pct * 2 - gap * 0.5, 90.0), np.maximum(10.0, 50 - pct * 0.3 - gap * 0.5),
         np.maximum(15.0, 70 - pct * 0.5), np.maximum(20.0, 65 - gap * 1.5)],
        default=40.0,
    )
    return np.round(base * 0.75 + idsim * 0.25, 1) if has_cross else np.round(base, 1)


def _apply_fee_consistency(decisions, threshold: float) -> dict:
    """Second look at matched pairs: if they share one consistent fee rate,
    flag any pair whose deduction differs from it (an unexplained fee that is
    still inside the amount tolerance). Mutates decisions in place."""
    pairs = [d for d in decisions
             if d.status == "matched" and d.amount_a and d.amount_b is not None]
    if len(pairs) < FEE_CHECK_MIN_PAIRS:
        return {"applied": False}
    pcts = np.array([(d.amount_a - d.amount_b) / d.amount_a * 100 for d in pairs])
    typical = float(np.median(pcts))
    mad = float(np.median(np.abs(pcts - typical)))
    if mad > FEE_CONSISTENCY_MAX_MAD_PP:
        return {"applied": False}  # no consistent fee structure to check against
    flagged = 0
    for d, p in zip(pairs, pcts):
        if abs(p - typical) <= FEE_DEVIATION_PP:
            continue
        rupees = abs(p - typical) / 100 * d.amount_a
        d.status = "exception"
        d.confidence = min(d.confidence, threshold - 0.1)
        d.flags = list(d.flags) + ["fee_deviation"]
        d.reason = (f"Unexpected fee — {p:.2f}% was deducted but this batch's typical rate is "
                    f"{typical:.2f}% (about {rupees:.2f} {'extra' if p > typical else 'less'}). "
                    f"Reference and date match, so it is the right transaction, but the amount needs review.")
        flagged += 1
    logger.info("Fee-consistency check: typical rate %.3f%%, %d pairs flagged", typical, flagged)
    return {"applied": True, "typical_pct": round(typical, 3), "flagged": flagged}


def reconcile(payments: pd.DataFrame, settlements: pd.DataFrame, auto_match_threshold: float | None = None) -> dict:
    """
    payments / settlements: dataframes already normalized to columns
    [id, amount, date, ...] via schema_mapper.apply_mapping()

    Returns: {"matched": [...], "exceptions": [...], "decisions": [Decision, ...]}

    Raises ValueError on malformed input rather than failing partway through
    with a confusing stack trace. auto_match_threshold overrides the config
    value (used by the threshold-sensitivity sweep in evaluate.py).
    """
    threshold = AUTO_MATCH_THRESHOLD if auto_match_threshold is None else auto_match_threshold
    _validate_input(payments, "payments", allow_empty=False)
    _validate_input(settlements, "settlements", allow_empty=True)

    logger.info(
        "Starting reconciliation: %d payment records vs %d settlement records",
        len(payments), len(settlements),
    )
    _start_time = time.perf_counter()

    has_cross = "internal_ref" in payments.columns
    P, S = payments.reset_index(drop=True), settlements.reset_index(drop=True)
    pamt, samt = P["amount"].to_numpy(float), S["amount"].to_numpy(float)
    pdt = pd.to_datetime(P["date"]).to_numpy("datetime64[ns]")
    sdt = pd.to_datetime(S["date"]).to_numpy("datetime64[ns]")
    pids, sids = P["id"].tolist(), S["id"].tolist()
    pamt_l, samt_l = pamt.tolist(), samt.tolist()
    pdate_s, sdate_s = P["date"].astype(str).tolist(), S["date"].astype(str).tolist()
    used = np.zeros(len(S), bool)

    if has_cross:
        pref_n = [_norm_ref(x) for x in P["internal_ref"]]
        sid_n = [_norm_ref(x) for x in S["id"]]
        pref_s, sid_s = _strip_common_prefix(pref_n), _strip_common_prefix(sid_n)
        index: dict[str, list[int]] = {}
        for j, key in enumerate(sid_n):
            if key not in _MISSING_REFS:
                index.setdefault(key, []).append(j)
    else:
        pref_raw = P["id"].astype(str).tolist()
        sid_raw = S["id"].astype(str).tolist()

    decisions: list[Decision] = []

    for i in range(len(P)):
        pid, pa, pd_s = pids[i], pamt_l[i], pdate_s[i]

        # ---- 0. refunds / chargebacks are not new payments -------------------
        # A negative amount (or a refund-style id) has no settlement to match; fuzzy-matching it
        # to some positive settlement would be a false match. Route it to a human with a clear label.
        if pa < 0 or "refund" in str(pid).lower():
            decisions.append(Decision(
                payment_id=pid, settlement_ref=None, status="exception", confidence=0.0,
                reason="Refund or chargeback-style entry — it reverses an earlier payment instead of "
                       "being settled on its own, so it was not matched. Check it against the original payment.",
                amount_a=pa, date_a=pd_s, flags=["refund"]))
            continue

        # ---- 1. which settlements is this payment even compared with? -------
        ci = None
        if has_cross and pref_n[i] not in _MISSING_REFS:
            hits = [j for j in index.get(pref_n[i], []) if not used[j]]
            if hits:
                ci, idsim_c = np.array(hits), np.full(len(hits), 100.0)   # exact reference
        if ci is None:
            if len(S):
                a, b = (pref_s[i], sid_s) if has_cross else (pref_raw[i], sid_raw)
                sim = process.cdist([a], b, scorer=fuzz.partial_ratio, dtype=np.float64)[0]
                amt_close = np.abs(pamt[i] - samt) / max(pamt[i], 1) < 0.05
                cand = ~used & ((sim >= MIN_ID_SIMILARITY_TO_CONSIDER) | amt_close)
                ci = np.flatnonzero(cand)
                idsim_c = sim[ci]
            else:
                ci = np.array([], dtype=int)

        if len(ci) == 0:
            decisions.append(Decision(
                payment_id=pid, settlement_ref=None, status="exception", confidence=0.0,
                reason="No plausible matching settlement record found at all — "
                       "payment may not have settled yet, or settlement data is missing.",
                amount_a=pa, date_a=pd_s))
            continue

        # ---- 2. pick the best candidate --------------------------------------
        sc = _scores(pamt[i], pdt[i], samt[ci], sdt[ci], idsim_c, has_cross)
        k = int(np.argmax(sc))
        best, idsim_best = int(ci[k]), float(idsim_c[k])
        sid, sa, sd_s = sids[best], samt_l[best], sdate_s[best]
        gap = abs(float(np.floor((sdt[best] - pdt[i]) / _DAY)))
        score, reason = _score_values(pa, sa, gap, idsim_best, has_cross)

        signature_counts: dict[tuple, int] = {}
        for j in ci[idsim_c >= STRONG_ID_SIMILARITY_FOR_DUPLICATE_CHECK]:
            key = (paise(samt_l[j]), sdate_s[j])
            signature_counts[key] = signature_counts.get(key, 0) + 1
        dup = max(signature_counts.values()) if signature_counts else 0

        kw = dict(payment_id=pid, settlement_ref=sid, amount_a=pa, amount_b=sa, date_a=pd_s, date_b=sd_s)
        pct = abs(pa - sa) / pa * 100 if pa else 100.0
        verified = (not has_cross) or idsim_best >= REFERENCE_MATCH_MIN_SIMILARITY

        # ---- 3. decide --------------------------------------------------------
        if dup > 1:
            # Never force-pick between identical-looking settlements: hides a real
            # risk (duplicate settlement / possible double payment).
            decisions.append(Decision(status="exception", confidence=score, reason=(
                f"Possible duplicate settlement — {dup} unclaimed settlement records share an "
                f"identical amount and date, so this payment's true match is ambiguous. "
                f"Needs human review before picking one."), **kw))
        elif not verified:
            # Amount/date fit is not enough: the reference codes disagree, so this is
            # most likely somebody else's settlement.
            decisions.append(Decision(status="exception", confidence=min(score, threshold - 0.1), reason=(
                f"Closest settlement by amount and date is {sid}, but its reference code "
                f"doesn't match this payment's ({idsim_best:.0f}% similar) — probably a different "
                f"transaction, so it was not matched. This payment may be missing from the "
                f"settlement file."), **kw))
        elif score >= threshold:
            used[best] = True
            decisions.append(Decision(status="matched", confidence=score, reason=reason, **kw))
        elif has_cross and pct <= AMOUNT_TOLERANCE_PCT and MAX_REASONABLE_DELAY_DAYS < gap <= MAX_LATE_SETTLEMENT_DAYS \
                and round(90 * 0.75 + idsim_best * 0.25, 1) >= threshold:
            # Same transaction (reference verified, amount normal) that simply
            # settled late. Matched, but flagged so a human can still see it.
            used[best] = True
            decisions.append(Decision(
                status="matched", confidence=round(90 * 0.75 + idsim_best * 0.25, 1), flags=["late_settlement"],
                reason=(f"Reference and amount match, but settlement arrived {int(gap)} days after "
                        f"payment — outside the normal {MAX_REASONABLE_DELAY_DAYS}-day window. "
                        f"Matched and flagged as a late settlement for review."), **kw))
        else:
            decisions.append(Decision(status="exception", confidence=score, reason=reason, **kw))

    fee_check = _apply_fee_consistency(decisions, threshold) if has_cross else {"applied": False}

    matched = [d for d in decisions if d.status == "matched"]
    exceptions = [d for d in decisions if d.status == "exception"]

    elapsed = time.perf_counter() - _start_time
    throughput = round(len(decisions) / elapsed, 1) if elapsed > 0 else len(decisions)
    late = sum(1 for d in matched if "late_settlement" in d.flags)

    logger.info(
        "Reconciliation complete: %d matched (%d flagged late), %d exceptions, match_rate=%.2f%%, "
        "elapsed=%.3fs, throughput=%.1f records/sec",
        len(matched), late, len(exceptions),
        round(100 * len(matched) / len(decisions), 2) if decisions else 0.0,
        elapsed, throughput,
    )

    return {
        "decisions": decisions,
        "matched": matched,
        "exceptions": exceptions,
        "late_flagged": late,
        "fee_check": fee_check,
        "match_rate": round(100 * len(matched) / len(decisions), 2) if decisions else 0.0,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_per_sec": throughput,
    }