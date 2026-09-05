"""
auto_reconciler.py
--------------------
THE UNIFICATION LAYER.

Verity has two reconciliation techniques:
  - Tolerance matching (matcher.py) — for when two sources have NO shared,
    reliable ID column. You have to guess which records correspond, using
    amount/date closeness and reference similarity.
  - Exact verification (three_way_reconciler.py) — for when a source
    explicitly provides a fee breakdown (gross, fee, tax, net) tied to a
    real shared ID. You can VERIFY instead of guess.

A real reconciliation system doesn't make a human pick between these. It
looks at what the data actually offers and decides:

  "Do these sources share a reliable ID, AND does one of them carry a fee
   breakdown (gross/fee/net)? If yes — verify exactly.
   If no — match by tolerance."

This module is genuinely schema-agnostic about WHICH company's export
format it's looking at — it was validated against two real, differently-
shaped schemas: Razorpay's settlement recon report (entity_id, fee, tax,
settlement_id, order_id) and Stripe's balance transaction report
(reporting_category, amount, fee, net, automatic_payout_id) — see
tests/test_auto_reconciler.py. Neither company's exact column names are
hardcoded anywhere in this file.
"""

import re
import time
import pandas as pd

from schema_mapper import map_schema
from matcher import reconcile as tolerance_reconcile
from logger import get_logger

logger = get_logger(__name__)

# Heuristic keyword hints for detecting a fee-breakdown structure, in the
# same spirit as schema_mapper's KEYWORD_HINTS — but for financial ROLES
# rather than id/amount/date. Deliberately broad: real companies name these
# columns very differently (Razorpay: "fee"/"tax"/"amount"; Stripe:
# "fee"/"net"/"amount" with no separate tax; a generic export might say
# "mdr_fee"/"gst"/"gross_amount"/"settled_amount").
FEE_ROLE_HINTS = {
    "gross": ["gross", "billed", "amount", "value", "original"],
    "fee": ["fee", "mdr", "commission", "charge"],
    "tax": ["tax", "gst", "vat"],
    "net": ["net", "settled", "credit", "payout", "deposit"],
}

# For grouping fee-breakdown rows into real-world settlement BATCHES — the
# same concept Razorpay calls "settlement_id" and Stripe calls
# "automatic_payout_id": many individual transactions get paid out to the
# bank together as one lump sum, and that lump sum is what actually shows
# up on a bank statement — never the individual transaction amounts.
BATCH_ID_HINTS = ["settlement_id", "settlement", "batch", "payout", "utr"]

# A bank statement doesn't have gross/fee/net — it has a free-text narration
# (what the deposit says it's for) and a credited amount. Real bank exports
# use very different words for these across banks: "description", "narration"
# (common in Indian bank exports), "particulars", "remarks".
BANK_DESC_HINTS = ["description", "narration", "particulars", "remarks", "details"]
BANK_CREDIT_HINTS = ["credit", "deposit", "received", "amount"]

MIN_ID_OVERLAP_FOR_EXACT_STRATEGY = 0.5  # at least half the IDs must appear in both sources
MATH_EPSILON = 0.02


def _score_columns_for_roles(columns: list[str], hints: dict) -> dict:
    """Same scoring approach as schema_mapper._heuristic_guess, applied to
    fee-related roles instead of id/amount/date."""
    mapping = {}
    used = set()
    for role, keywords in hints.items():
        best_col, best_score = None, 0
        for col in columns:
            if col in used:
                continue
            col_lower = col.lower()
            score = sum(1 for kw in keywords if kw in col_lower)
            if score > best_score:
                best_col, best_score = col, score
        if best_col:
            mapping[role] = best_col
            used.add(best_col)
    return mapping


def detect_fee_breakdown_columns(df: pd.DataFrame) -> dict | None:
    """
    Returns {"gross": col, "fee": col, "net": col, "tax": col (optional)}
    if this dataframe looks like a fee-breakdown source (a real payment
    gateway settlement/balance report), or None if it doesn't.

    Requires at minimum gross + fee + net to be distinctly identified —
    tax is optional, since not every provider reports it separately
    (Stripe's balance transactions don't split out tax; Razorpay's do).
    """
    roles = _score_columns_for_roles(list(df.columns), FEE_ROLE_HINTS)
    required = {"gross", "fee", "net"}
    if not required.issubset(roles.keys()):
        return None

    # Sanity-check: these columns should actually be numeric, not just
    # named suggestively (e.g. a "gross_description" text column).
    for role in required:
        if not pd.api.types.is_numeric_dtype(pd.to_numeric(df[roles[role]], errors="coerce")):
            return None
        if pd.to_numeric(df[roles[role]], errors="coerce").isna().all():
            return None

    return roles


def detect_batch_id_column(fee_df: pd.DataFrame, exclude: set) -> str | None:
    """
    Finds the column that groups individual transactions into a real-world
    settlement batch (Razorpay's settlement_id, Stripe's
    automatic_payout_id). This is what actually gets deposited to the bank
    as one lump sum — never the individual transaction amount.
    """
    candidates = [c for c in fee_df.columns if c not in exclude]
    scored = _score_columns_for_roles(candidates, {"batch": BATCH_ID_HINTS})
    return scored.get("batch")


def detect_bank_columns(bank_df: pd.DataFrame) -> dict | None:
    """
    Returns {"description": col, "credit": col} if this dataframe looks
    like a real bank statement — a free-text narration column plus a
    credited-amount column — or None otherwise. Every bank names these
    differently (a real Indian bank export commonly says "Narration" and
    "Credit"; a US-style export might say "Description" and "Amount").
    """
    desc_col = _score_columns_for_roles(list(bank_df.columns), {"description": BANK_DESC_HINTS}).get("description")
    if desc_col is None:
        return None
    remaining = [c for c in bank_df.columns if c != desc_col]
    credit_col = _score_columns_for_roles(remaining, {"credit": BANK_CREDIT_HINTS}).get("credit")
    if credit_col is None:
        return None
    if pd.to_numeric(bank_df[credit_col], errors="coerce").isna().all():
        return None
    return {"description": desc_col, "credit": credit_col}


def _find_id_column(df: pd.DataFrame) -> str | None:
    try:
        mapping = map_schema(df, "auto_detect")
        return mapping.get("id")
    except ValueError:
        return None


def _best_matching_id_column(df: pd.DataFrame, target_ids: set) -> tuple[str | None, float]:
    """
    Real settlement reports often have MULTIPLE id-like columns (e.g.
    Razorpay's report has both 'entity_id' — the payment/refund/transfer's
    own ID — and 'order_id' — the originating order). Only one of them is
    the correct join key against a given reference source, and it isn't
    always the one schema_mapper's single-best-guess would pick.

    Instead of trusting one guessed column, this tries every column in df
    and returns whichever ACTUALLY overlaps best with the reference IDs —
    the empirically correct join key, not just the most plausibly-named one.
    """
    if not target_ids:
        return None, 0.0

    best_col, best_overlap = None, 0.0
    for col in df.columns:
        overlap = compute_id_overlap(target_ids_as_series := pd.Series(list(target_ids)), df[col])
        # compute_id_overlap(a, b) = fraction of a found in b; we want the
        # fraction of target_ids found in this column, so pass target as 'a'
        if overlap > best_overlap:
            best_col, best_overlap = col, overlap
    return best_col, best_overlap


def compute_id_overlap(series_a: pd.Series, series_b: pd.Series) -> float:
    """What fraction of series_a's values also appear in series_b — the
    simplest possible test for 'do these two sources share a real key'."""
    set_a = set(series_a.astype(str))
    set_b = set(series_b.astype(str))
    if not set_a:
        return 0.0
    return len(set_a & set_b) / len(set_a)


def choose_strategy(df_a: pd.DataFrame, df_b: pd.DataFrame) -> dict:
    """
    The core decision. Looks at both dataframes and decides which
    reconciliation technique actually fits the data provided — never
    assumes, always checks.
    """
    fee_cols_a = detect_fee_breakdown_columns(df_a)
    fee_cols_b = detect_fee_breakdown_columns(df_b)

    # The "reference" side is whichever dataframe does NOT look like the
    # fee-breakdown source — that's the one whose id column we trust from
    # schema_mapper's single best guess (it typically has only one
    # plausible id column, unlike a gateway report with several).
    if fee_cols_a and not fee_cols_b:
        fee_df, fee_cols, reference_df = df_a, fee_cols_a, df_b
    elif fee_cols_b and not fee_cols_a:
        fee_df, fee_cols, reference_df = df_b, fee_cols_b, df_a
    else:
        fee_df, fee_cols, reference_df = None, None, None

    id_overlap = 0.0
    fee_id_col = None
    if reference_df is not None:
        ref_id_col = _find_id_column(reference_df)
        if ref_id_col:
            fee_id_col, id_overlap = _best_matching_id_column(fee_df, set(reference_df[ref_id_col].astype(str)))

    if fee_cols is not None and id_overlap >= MIN_ID_OVERLAP_FOR_EXACT_STRATEGY:
        # Detect the settlement/batch/payout ID column too (if one exists) —
        # needed so verify_exact() can recognize a "PENDING" status BEFORE
        # doing any math check on that row. Without this, a row still
        # awaiting settlement (money hasn't moved yet) gets silently run
        # through the same math/unmatched checks as a fully settled one.
        batch_id_col = detect_batch_id_column(fee_df, exclude=set(fee_cols.values()) | {fee_id_col})

        return {
            "strategy": "exact",
            "reason": (
                f"Found a fee breakdown ({', '.join(f'{k}→{v}' for k, v in fee_cols.items())}) "
                f"AND {id_overlap:.0%} of reference IDs are found in column '{fee_id_col}' — "
                f"exact arithmetic verification is possible, so it's used instead of guessing."
            ),
            "fee_columns": fee_cols,
            "fee_id_column": fee_id_col,
            "batch_id_column": batch_id_col,
            "id_overlap": round(id_overlap, 3),
            "fee_source_is_a": fee_df is df_a,
        }

    return {
        "strategy": "tolerance",
        "reason": (
            f"No usable fee breakdown found, or IDs don't reliably overlap "
            f"({id_overlap:.0%} overlap) — falling back to tolerance-based "
            f"matching on amount, date, and reference similarity."
        ),
        "fee_columns": None,
        "fee_id_column": None,
        "id_overlap": round(id_overlap, 3),
        "fee_source_is_a": None,
    }


def _find_date_column(df: pd.DataFrame) -> str | None:
    """Best-effort date column detection for display purposes (e.g.
    'order_created_on', 'created_at', 'settlement_date') — reuses the same
    keyword spirit as schema_mapper, kept local here since this is only
    used for showing dates on exact-verification rows, not for matching."""
    hints = ["date", "created_at", "settled_at", "created_on", "timestamp"]
    scored = _score_columns_for_roles(list(df.columns), {"date": hints})
    return scored.get("date")


def verify_exact(reference_df: pd.DataFrame, fee_df: pd.DataFrame, fee_cols: dict, fee_id_col: str, batch_id_col: str = None) -> dict:
    """
    Generalized exact-math verification — the same priority-ordered logic
    as three_way_reconciler.verify_orders, but parameterized by whatever
    column names were actually detected, instead of hardcoded sheet-
    specific names. Works whether or not a separate tax column exists.

    fee_id_col is passed in explicitly (found via _best_matching_id_column
    in choose_strategy) rather than re-derived here, since a fee-breakdown
    source can have multiple id-like columns and only one is the correct
    join key against this specific reference source.

    batch_id_col (optional) is checked FIRST, before any math — a row whose
    settlement/batch ID literally says "PENDING" hasn't had money move yet,
    so running it through the math/unmatched checks would be meaningless
    (and, worse, silently misclassify it as verified or a math error).
    """
    ref_id_col = _find_id_column(reference_df)
    if ref_id_col is None:
        raise ValueError("Could not identify an ID column in the reference source for exact verification.")

    ref_date_col = _find_date_column(reference_df)
    fee_date_col = _find_date_column(fee_df)

    ref_ids = set(reference_df[ref_id_col].astype(str))
    ref_dates_by_id = (
        dict(zip(reference_df[ref_id_col].astype(str), reference_df[ref_date_col]))
        if ref_date_col else {}
    )
    has_tax = "tax" in fee_cols
    _start_time = time.perf_counter()

    results = []
    for _, row in fee_df.iterrows():
        order_id = str(row[fee_id_col])
        gross = float(row[fee_cols["gross"]])
        fee = float(row[fee_cols["fee"]])
        tax = float(row[fee_cols["tax"]]) if has_tax else 0.0
        net = float(row[fee_cols["net"]])
        expected_net = round(gross - fee - tax, 2)
        diff = round(net - expected_net, 2)
        date_a = ref_dates_by_id.get(order_id)
        date_b = row[fee_date_col] if fee_date_col else None

        if batch_id_col and str(row.get(batch_id_col, "")).strip().upper() == "PENDING":
            status = "pending_settlement"
            reason = (
                f"Settlement is still PENDING for {order_id} — the bank deposit hasn't "
                f"happened yet. This is a timing state, not a failure."
            )
        elif gross < 0 or "refund" in order_id.lower():
            base_id = re.sub(r"_?refund$", "", order_id, flags=re.IGNORECASE)
            status = "prior_period_deduction"
            reason = (
                f"Negative/refund-style entry for {order_id} (₹{net:.2f}) — "
                f"{'matches an existing record' if base_id in ref_ids else 'no matching record in the reference source'}, "
                f"consistent with a refund or chargeback rather than a new transaction."
            )
        elif abs(diff) > MATH_EPSILON:
            status = "math_error"
            reason = (
                f"{gross:.2f} - {fee:.2f} (fee)" + (f" - {tax:.2f} (tax)" if has_tax else "") +
                f" = {expected_net:.2f} expected, but reported net is {net:.2f} "
                f"— an unexplained {'shortfall' if diff < 0 else 'excess'} of ₹{abs(diff):.2f}."
            )
        elif order_id not in ref_ids:
            status = "unmatched_to_reference"
            reason = f"Fee breakdown settles {order_id} but it has no matching record in the reference source."
        else:
            status = "verified"
            reason = "Gross minus fee" + (" minus tax" if has_tax else "") + " matches the reported net exactly."

        results.append({
            "order_id": order_id, "status": status, "reason": reason,
            "date_a": date_a, "date_b": date_b,
            "gross": gross, "fee": fee, "tax": tax if has_tax else None, "net": net,
            "expected_net": expected_net, "diff": diff,
        })

    status_counts = {}
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    elapsed = time.perf_counter() - _start_time
    throughput = round(len(results) / elapsed, 1) if elapsed > 0 else len(results)

    return {
        "orders": results, "status_counts": status_counts, "total": len(results),
        "elapsed_seconds": round(elapsed, 3), "throughput_per_sec": throughput,
    }


def verify_settlement_batches(
    fee_df: pd.DataFrame, fee_cols: dict, batch_id_col: str,
    bank_df: pd.DataFrame, bank_cols: dict,
) -> dict:
    """
    THE THIRD SOURCE. Groups fee_df by its real-world settlement batch and
    checks the total against what actually landed in the bank — the same
    two-stage pattern real finance-ops reconciliation uses: (1) does each
    transaction's math check out, (2) did the promised lump sum actually
    arrive. Bank lines that don't reference any known batch (rent, a cloud
    bill) are correctly excluded, never treated as a missing settlement.
    """
    bank_desc = bank_df[bank_cols["description"]].astype(str)
    batches = []
    matched_bank_row_indices = set()

    # Rows still awaiting settlement share the literal placeholder value
    # "PENDING" instead of a real batch ID — grouping those together would
    # create a fake "PENDING" batch and incorrectly report it as having no
    # bank deposit, when the honest answer is simply "not applicable yet".
    settled_df = fee_df[fee_df[batch_id_col].astype(str).str.strip().str.upper() != "PENDING"]
    pending_count = len(fee_df) - len(settled_df)

    for batch_id, group in settled_df.groupby(batch_id_col):
        reported_total = round(float(group[fee_cols["net"]].sum()), 2)
        match_mask = bank_desc.str.contains(re.escape(str(batch_id)), case=False, na=False)
        matched_rows = bank_df[match_mask]

        if matched_rows.empty:
            batches.append({
                "batch_id": str(batch_id), "reported_total": reported_total,
                "bank_deposit": None, "diff": None, "status": "no_bank_record",
                "reason": f"No bank deposit found yet referencing batch {batch_id}.",
            })
            continue

        matched_bank_row_indices.update(matched_rows.index)
        deposit = round(float(matched_rows[bank_cols["credit"]].sum()), 2)
        diff = round(deposit - reported_total, 2)

        if abs(diff) > MATH_EPSILON:
            batches.append({
                "batch_id": str(batch_id), "reported_total": reported_total,
                "bank_deposit": deposit, "diff": diff, "status": "mismatch",
                "reason": (
                    f"Batch {batch_id} should total ₹{reported_total:.2f}, but the bank "
                    f"deposit was ₹{deposit:.2f} — a ₹{abs(diff):.2f} "
                    f"{'shortfall' if diff < 0 else 'excess'}."
                ),
            })
        else:
            batches.append({
                "batch_id": str(batch_id), "reported_total": reported_total,
                "bank_deposit": deposit, "diff": diff, "status": "verified",
                "reason": f"Bank deposit of ₹{deposit:.2f} matches the reported batch total exactly.",
            })

    unrelated_rows = bank_df.loc[~bank_df.index.isin(matched_bank_row_indices)]
    unrelated = [
        {"description": str(row[bank_cols["description"]]), "amount": row.get(bank_cols["credit"])}
        for _, row in unrelated_rows.iterrows()
    ]

    return {"batches": batches, "unrelated_bank_lines": unrelated, "pending_count": pending_count}


def run_auto_reconciliation(df_a: pd.DataFrame, df_b: pd.DataFrame, bank_df: pd.DataFrame = None) -> dict:
    """
    Single entry point: decide the strategy, run it, and return a result
    shape that always includes which strategy was chosen and why — so the
    decision itself is auditable, not a black box.

    bank_df is OPTIONAL — a genuine third source (a real bank statement).
    When provided AND exact verification was chosen AND a settlement/batch
    ID can be identified, this chains a second, real multi-source check:
    order-level math (source A vs source B) THEN batch-level bank
    verification (source B's batches vs source C's actual deposits) — two
    pairwise checks stacked, which is how real multi-source reconciliation
    actually works, not one bigger three-way algorithm.
    """
    decision = choose_strategy(df_a, df_b)
    logger.info("Auto-reconciliation strategy chosen: %s (%s)", decision["strategy"], decision["reason"])

    if decision["strategy"] == "exact":
        fee_df, reference_df = (df_a, df_b) if decision["fee_source_is_a"] else (df_b, df_a)
        result = verify_exact(
            reference_df, fee_df, decision["fee_columns"], decision["fee_id_column"],
            batch_id_col=decision.get("batch_id_column"),
        )

        batch_result = None
        if bank_df is not None:
            batch_id_col = decision.get("batch_id_column")
            bank_cols = detect_bank_columns(bank_df)
            if batch_id_col and bank_cols:
                batch_result = verify_settlement_batches(fee_df, decision["fee_columns"], batch_id_col, bank_df, bank_cols)
                batch_result["batch_id_column"] = batch_id_col
                batch_result["bank_columns"] = bank_cols
                logger.info("Multi-source batch verification ran using batch column '%s'.", batch_id_col)
            else:
                logger.info(
                    "Bank statement provided but could not identify a batch ID column (%s) "
                    "or usable bank columns (%s) — skipping batch verification.",
                    batch_id_col, bank_cols,
                )

        return {"decision": decision, "exact_result": result, "tolerance_result": None, "batch_result": batch_result}

    from schema_mapper import apply_mapping
    mapping_a = map_schema(df_a, "source_a")
    mapping_b = map_schema(df_b, "source_b")
    normalized_a = apply_mapping(df_a, mapping_a)
    normalized_b = apply_mapping(df_b, mapping_b)
    result = tolerance_reconcile(normalized_a, normalized_b)
    # A bank statement only makes sense to reconcile against a real
    # settlement/fee structure — tolerance matching has no batch concept,
    # so a third file provided here is honestly not usable and is ignored.
    return {"decision": decision, "exact_result": None, "tolerance_result": result, "batch_result": None}