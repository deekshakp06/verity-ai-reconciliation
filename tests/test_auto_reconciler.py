"""
test_auto_reconciler.py
--------------------------
Proves the auto-detection layer genuinely generalizes across different
real-world company schemas — not just the one it was built against.

Tested against two authentic, differently-shaped schemas taken from each
company's own public documentation:
  - Razorpay's settlement recon report (entity_id, fee, tax, settlement_id,
    order_id) — has a separate tax column, and TWO id-like columns where
    only one is the correct join key.
  - Stripe's balance transaction report (reporting_category, amount, fee,
    net, automatic_payout_id) — no separate tax column at all.

Neither company's exact column names are hardcoded in auto_reconciler.py —
if they were, only one of these two test groups could possibly pass.
"""

import os
import sys
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_reconciler import (
    choose_strategy,
    run_auto_reconciliation,
    detect_fee_breakdown_columns,
    compute_id_overlap,
)


# ---------------------------------------------------------------------------
# Fee-breakdown detection
# ---------------------------------------------------------------------------

def test_detects_razorpay_shaped_fee_columns():
    df = pd.DataFrame([{
        "entity_id": "pay_1", "order_id": "order_1", "amount": 1000,
        "fee": 20, "tax": 3.6, "credit": 976.4, "settlement_id": "setl_1",
    }])
    cols = detect_fee_breakdown_columns(df)
    assert cols is not None
    assert cols["gross"] == "amount"
    assert cols["fee"] == "fee"
    assert cols["tax"] == "tax"
    assert cols["net"] == "credit"


def test_detects_stripe_shaped_fee_columns_with_no_tax():
    df = pd.DataFrame([{
        "reporting_category": "charge", "id": "ch_1", "amount": 1000,
        "fee": 29, "net": 971, "automatic_payout_id": "po_1",
    }])
    cols = detect_fee_breakdown_columns(df)
    assert cols is not None
    assert cols["gross"] == "amount"
    assert cols["fee"] == "fee"
    assert cols["net"] == "net"
    assert "tax" not in cols  # Stripe genuinely doesn't split this out


def test_two_id_only_file_does_not_look_like_fee_breakdown():
    df = pd.DataFrame([{"txn_id": "TXN1", "amount_inr": 500, "payment_date": "2026-08-01"}])
    assert detect_fee_breakdown_columns(df) is None


# ---------------------------------------------------------------------------
# ID overlap
# ---------------------------------------------------------------------------

def test_id_overlap_full_match():
    a = pd.Series(["A1", "A2", "A3"])
    b = pd.Series(["A1", "A2", "A3", "A4"])
    assert compute_id_overlap(a, b) == 1.0


def test_id_overlap_no_match():
    a = pd.Series(["A1", "A2"])
    b = pd.Series(["B1", "B2"])
    assert compute_id_overlap(a, b) == 0.0


# ---------------------------------------------------------------------------
# End-to-end strategy selection + verification, on REAL schemas
# ---------------------------------------------------------------------------

def _make_razorpay_shaped_pair():
    settlement = pd.DataFrame([
        {"entity_id": "pay_1", "type": "payment", "order_id": "order_1",
         "amount": 1000.0, "fee": 20.0, "tax": 3.6, "credit": 976.4,
         "settlement_id": "setl_1", "created_at": "2026-08-01"},
        {"entity_id": "pay_2", "type": "payment", "order_id": "order_2",
         "amount": 2000.0, "fee": 40.0, "tax": 7.2, "credit": 1952.8,
         "settlement_id": "setl_1", "created_at": "2026-08-02"},
    ])
    internal = pd.DataFrame([
        {"internal_order_ref": "order_1", "billed_amount": 1000.0, "order_created_on": "2026-08-01"},
        {"internal_order_ref": "order_2", "billed_amount": 2000.0, "order_created_on": "2026-08-02"},
    ])
    return internal, settlement


def _make_stripe_shaped_pair():
    settlement = pd.DataFrame([
        {"reporting_category": "charge", "id": "ch_1", "amount": 5000.0,
         "fee": 175.0, "net": 4825.0, "automatic_payout_id": "po_1", "created": "2026-08-01"},
        {"reporting_category": "charge", "id": "ch_2", "amount": 9900.0,
         "fee": 317.0, "net": 9583.0, "automatic_payout_id": "po_1", "created": "2026-08-02"},
    ])
    internal = pd.DataFrame([
        {"our_charge_ref": "ch_1", "billed_cents": 5000.0, "billed_on": "2026-08-01"},
        {"our_charge_ref": "ch_2", "billed_cents": 9900.0, "billed_on": "2026-08-02"},
    ])
    return internal, settlement


def test_razorpay_shaped_data_selects_exact_strategy_via_correct_join_key():
    """Razorpay's report has TWO id-like columns (entity_id, order_id) —
    only order_id is the correct join key against internal records. This
    guards against silently picking the wrong one."""
    internal, settlement = _make_razorpay_shaped_pair()
    decision = choose_strategy(internal, settlement)
    assert decision["strategy"] == "exact"
    assert decision["fee_id_column"] == "order_id"
    assert decision["id_overlap"] == 1.0


def test_razorpay_shaped_data_verifies_correctly_end_to_end():
    internal, settlement = _make_razorpay_shaped_pair()
    result = run_auto_reconciliation(internal, settlement)
    assert result["exact_result"] is not None
    assert result["exact_result"]["status_counts"] == {"verified": 2}


def test_stripe_shaped_data_selects_exact_strategy_with_no_tax_column():
    internal, settlement = _make_stripe_shaped_pair()
    decision = choose_strategy(internal, settlement)
    assert decision["strategy"] == "exact"
    assert decision["fee_id_column"] == "id"


def test_stripe_shaped_data_verifies_correctly_end_to_end():
    internal, settlement = _make_stripe_shaped_pair()
    result = run_auto_reconciliation(internal, settlement)
    assert result["exact_result"] is not None
    assert result["exact_result"]["status_counts"] == {"verified": 2}


def test_exact_strategy_catches_a_real_math_error_in_stripe_shaped_data():
    internal, settlement = _make_stripe_shaped_pair()
    settlement.loc[0, "net"] = 4800.0  # was 4825.0 — introduce a ₹25 discrepancy
    result = run_auto_reconciliation(internal, settlement)
    statuses = {o["order_id"]: o["status"] for o in result["exact_result"]["orders"]}
    assert statuses["ch_1"] == "math_error"
    assert statuses["ch_2"] == "verified"


def test_no_fee_breakdown_falls_back_to_tolerance_strategy():
    """The original schema-agnostic demo scenario — two plain files with no
    fee breakdown at all — must still fall back to tolerance matching."""
    payments = pd.DataFrame([{"txn_id": "TXN1", "amount_inr": 500.0, "payment_date": "2026-08-01"}])
    settlements = pd.DataFrame([{"reference_no": "TXN1", "settled_amount": 495.0, "settlement_date": "2026-08-03"}])
    decision = choose_strategy(payments, settlements)
    assert decision["strategy"] == "tolerance"

    result = run_auto_reconciliation(payments, settlements)
    assert result["tolerance_result"] is not None
    assert result["exact_result"] is None


def test_full_pipeline_against_real_sample_files_if_present():
    """End-to-end check against the actual sample CSVs generated from real
    company schemas, if present in this environment."""
    razorpay_settlement = os.path.join("data", "sample_razorpay_real_schema_settlement.csv")
    razorpay_internal = os.path.join("data", "sample_internal_orders.csv")
    stripe_settlement = os.path.join("data", "sample_stripe_real_schema_balance.csv")
    stripe_internal = os.path.join("data", "sample_internal_orders_stripe.csv")

    if not all(os.path.exists(p) for p in [razorpay_settlement, razorpay_internal, stripe_settlement, stripe_internal]):
        pytest.skip("Real-schema sample files not present in this environment")

    r_decision = choose_strategy(pd.read_csv(razorpay_internal), pd.read_csv(razorpay_settlement))
    assert r_decision["strategy"] == "exact"

    s_decision = choose_strategy(pd.read_csv(stripe_internal), pd.read_csv(stripe_settlement))
    assert s_decision["strategy"] == "exact"


# ---------------------------------------------------------------------------
# Multi-source (third source: a real bank statement) — chains a second,
# genuine pairwise check (settlement batches vs. actual bank deposits) on
# top of the order-level exact verification, exactly the two-stage pattern
# real finance-ops reconciliation actually uses.
# ---------------------------------------------------------------------------

def _make_three_source_scenario():
    internal = pd.DataFrame([
        {"internal_order_ref": "order_1", "billed_amount": 1000.0, "order_created_on": "2026-08-01"},
        {"internal_order_ref": "order_2", "billed_amount": 2000.0, "order_created_on": "2026-08-01"},
        {"internal_order_ref": "order_3", "billed_amount": 500.0, "order_created_on": "2026-08-02"},
    ])
    settlement = pd.DataFrame([
        {"entity_id": "pay_1", "order_id": "order_1", "amount": 1000.0, "fee": 20.0, "tax": 3.6, "credit": 976.4, "settlement_id": "setl_A"},
        {"entity_id": "pay_2", "order_id": "order_2", "amount": 2000.0, "fee": 40.0, "tax": 7.2, "credit": 1952.8, "settlement_id": "setl_A"},
        {"entity_id": "pay_3", "order_id": "order_3", "amount": 500.0, "fee": 10.0, "tax": 1.8, "credit": 488.2, "settlement_id": "setl_B"},
    ])
    bank = pd.DataFrame([
        {"date": "2026-08-03", "narration": "NEFT CR-RAZORPAY-setl_A batch payout", "credit": 2929.2},
        {"date": "2026-08-04", "narration": "NEFT CR-RAZORPAY-setl_B batch payout", "credit": 488.2},
        {"date": "2026-08-03", "narration": "OFFICE RENT AUGUST", "credit": 0},
        {"date": "2026-08-04", "narration": "AWS CLOUD SERVICES", "credit": 0},
    ])
    return internal, settlement, bank


def test_multi_source_batch_verification_matches_real_bank_deposits():
    internal, settlement, bank = _make_three_source_scenario()
    result = run_auto_reconciliation(internal, settlement, bank_df=bank)
    assert result["batch_result"] is not None
    statuses = {b["batch_id"]: b["status"] for b in result["batch_result"]["batches"]}
    assert statuses == {"setl_A": "verified", "setl_B": "verified"}


def test_multi_source_excludes_unrelated_bank_activity():
    internal, settlement, bank = _make_three_source_scenario()
    result = run_auto_reconciliation(internal, settlement, bank_df=bank)
    unrelated_descriptions = {u["description"] for u in result["batch_result"]["unrelated_bank_lines"]}
    assert unrelated_descriptions == {"OFFICE RENT AUGUST", "AWS CLOUD SERVICES"}


def test_multi_source_detects_a_genuine_batch_mismatch():
    internal, settlement, _ = _make_three_source_scenario()
    internal = internal[internal["internal_order_ref"] == "order_3"]
    settlement = settlement[settlement["settlement_id"] == "setl_B"]  # single-order batch, easy to verify by hand
    bank_short = pd.DataFrame([{"date": "2026-08-03", "narration": "NEFT CR-RAZORPAY-setl_B", "credit": 400.0}])
    result = run_auto_reconciliation(internal, settlement, bank_df=bank_short)
    assert result["decision"]["strategy"] == "exact"
    batch = result["batch_result"]["batches"][0]
    assert batch["status"] == "mismatch"
    assert batch["reported_total"] == 488.2
    assert batch["diff"] == round(400.0 - 488.2, 2)


def test_multi_source_gracefully_skipped_when_bank_file_unusable():
    internal, settlement, _ = _make_three_source_scenario()
    unusable_bank = pd.DataFrame([{"col1": "x", "col2": 1}])
    result = run_auto_reconciliation(internal, settlement, bank_df=unusable_bank)
    assert result["batch_result"] is None
    assert result["exact_result"] is not None  # order-level check still runs fine


def test_multi_source_ignored_when_tolerance_strategy_selected():
    """A bank statement only makes sense alongside a real fee/settlement
    structure — when no such structure exists, a third file is honestly
    unusable and should be ignored, not crash."""
    payments = pd.DataFrame([{"txn_id": "TXN1", "amount_inr": 500.0, "payment_date": "2026-08-01"}])
    settlements = pd.DataFrame([{"reference_no": "TXN1", "settled_amount": 495.0, "settlement_date": "2026-08-03"}])
    bank = pd.DataFrame([{"date": "2026-08-03", "narration": "NEFT CR", "credit": 495.0}])
    result = run_auto_reconciliation(payments, settlements, bank_df=bank)
    assert result["decision"]["strategy"] == "tolerance"
    assert result["batch_result"] is None


# ---------------------------------------------------------------------------
# Ground-truth validation for the exact-verification engine
# ---------------------------------------------------------------------------

def test_exact_engine_ground_truth_validation_on_real_sample_if_present():
    """The multi-source demo data has known-planted issues — the exact
    engine's classifications should be measurably validated against them,
    not just assumed correct because it's arithmetic."""
    from evaluate import evaluate_exact_against_ground_truth, has_multi_source_ground_truth

    internal_path = os.path.join("data", "sample_internal_orders.csv")
    settlement_path = os.path.join("data", "sample_razorpay_real_schema_settlement.csv")
    bank_path = os.path.join("data", "sample_razorpay_bank_statement.csv")

    if not (os.path.exists(internal_path) and os.path.exists(settlement_path) and has_multi_source_ground_truth()):
        pytest.skip("Multi-source demo data or ground truth not present in this environment")

    internal = pd.read_csv(internal_path)
    settlement = pd.read_csv(settlement_path)
    bank = pd.read_csv(bank_path) if os.path.exists(bank_path) else None

    result = run_auto_reconciliation(internal, settlement, bank_df=bank)
    metrics = evaluate_exact_against_ground_truth(result["exact_result"]["orders"])

    assert metrics is not None
    assert metrics["accuracy"] == 100.0  # every planted issue should be classified correctly
    assert metrics["mistakes"] == []
