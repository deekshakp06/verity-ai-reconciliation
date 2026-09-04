"""
test_three_way_reconciler.py
------------------------------
Tests the exact-verification engine using small synthetic fixtures that
mirror each planted scenario, plus one end-to-end test against the real
sample workbook if it's present in data/.
"""

import os
import sys
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from three_way_reconciler import (
    verify_orders,
    verify_settlement_batches,
    find_unrelated_bank_activity,
    run_three_way_reconciliation,
)

SAMPLE_XLSX = os.path.join("data", "three_way_sample.xlsx")


def make_sales(rows):
    return pd.DataFrame(rows, columns=["Date", "Order_ID", "Customer_Name", "Gross_Amount_Billed", "Status"])


def make_gateway(rows):
    return pd.DataFrame(rows, columns=[
        "Transaction_Date", "Order_ID", "Payment_Method", "Settlement_ID",
        "Gross_Amount", "MDR_Fee", "Tax_on_Fee", "Net_Amount",
    ])


def test_math_error_is_caught_exactly():
    sales = make_sales([["2026-08-10", "ORD_1", "A", 4000, "Paid"]])
    # 4000 - 80 - 14.4 = 3905.6, but Net_Amount is wrong by exactly 15
    gateway = make_gateway([["2026-08-10", "ORD_1", "Card", "SETTL_1", 4000, 80.0, 14.4, 3890.6]])
    verdicts = verify_orders(sales, gateway)
    assert verdicts[0].status == "math_error"
    assert verdicts[0].math_diff == -15.0


def test_premium_card_fee_is_explained_not_flagged():
    sales = make_sales([["2026-08-11", "ORD_2", "B", 999, "Paid"]])
    # Diners Club at exactly 3% — should be VERIFIED, not an anomaly
    gateway = make_gateway([["2026-08-11", "ORD_2", "Diners Club Credit", "SETTL_2", 999, 29.97, 5.39, 963.64]])
    verdicts = verify_orders(sales, gateway)
    assert verdicts[0].status == "verified"
    assert "premium card rate" in verdicts[0].reason.lower() or "3.0%" in verdicts[0].reason


def test_refund_with_no_current_sales_record_is_prior_period_deduction():
    sales = make_sales([["2026-08-10", "ORD_1", "A", 500, "Paid"]])  # ORD_0850 not present at all
    gateway = make_gateway([["2026-08-11", "ORD_0850_REFUND", "Card", "SETTL_2", -1500, 0.0, 0.0, -1500.0]])
    verdicts = verify_orders(sales, gateway)
    assert verdicts[0].status == "prior_period_deduction"
    assert "chargeback" in verdicts[0].reason.lower() or "prior" in verdicts[0].reason.lower()


def test_pending_settlement_is_not_treated_as_failure():
    sales = make_sales([["2026-08-14", "ORD_9", "C", 500, "Paid"]])
    gateway = make_gateway([["2026-08-14", "ORD_9", "UPI / Card", "PENDING", 500, 10.0, 1.8, 488.2]])
    verdicts = verify_orders(sales, gateway)
    assert verdicts[0].status == "pending_settlement"
    assert "not a failure" in verdicts[0].reason.lower()


def test_unmatched_to_sales_is_flagged():
    sales = make_sales([["2026-08-10", "ORD_1", "A", 500, "Paid"]])
    gateway = make_gateway([["2026-08-10", "ORD_999", "Card", "SETTL_1", 500, 10.0, 1.8, 488.2]])
    verdicts = verify_orders(sales, gateway)
    assert verdicts[0].status == "unmatched_to_sales"


def test_settlement_batch_matches_bank_deposit():
    gateway = make_gateway([
        ["2026-08-10", "ORD_1", "Card", "SETTL_1", 500, 10.0, 1.8, 488.2],
        ["2026-08-10", "ORD_2", "Card", "SETTL_1", 500, 10.0, 1.8, 488.2],
    ])
    bank = pd.DataFrame([
        {"Date": "2026-08-11", "Description": "NEFT CR-RAZORPAY-SETTL_1", "Credit": 976.4, "Debit": 0},
    ])
    batches = verify_settlement_batches(gateway, bank)
    assert batches[0].status == "verified"
    assert batches[0].reported_net_total == 976.4


def test_settlement_batch_mismatch_is_detected():
    gateway = make_gateway([
        ["2026-08-10", "ORD_1", "Card", "SETTL_1", 500, 10.0, 1.8, 488.2],
    ])
    bank = pd.DataFrame([
        {"Date": "2026-08-11", "Description": "NEFT CR-RAZORPAY-SETTL_1", "Credit": 400.0, "Debit": 0},
    ])
    batches = verify_settlement_batches(gateway, bank)
    assert batches[0].status == "mismatch"
    assert batches[0].diff is not None


def test_unrelated_bank_lines_are_excluded_not_flagged():
    bank = pd.DataFrame([
        {"Date": "2026-08-10", "Description": "OFFICE RENT AUG", "Credit": 0, "Debit": 15000},
        {"Date": "2026-08-11", "Description": "NEFT CR-RAZORPAY-SETTL_1", "Credit": 500, "Debit": 0},
    ])
    unrelated = find_unrelated_bank_activity(bank)
    assert len(unrelated) == 1
    assert unrelated[0]["Description"] == "OFFICE RENT AUG"


def test_full_pipeline_against_real_sample_file():
    """End-to-end check against the actual provided sample workbook, if present."""
    if not os.path.exists(SAMPLE_XLSX):
        pytest.skip("Sample three-way Excel file not present in this environment")

    result = run_three_way_reconciliation(SAMPLE_XLSX)
    counts = result["status_counts"]

    assert counts.get("math_error", 0) == 1
    assert counts.get("prior_period_deduction", 0) == 1
    assert counts.get("pending_settlement", 0) == 11
    assert result["total_orders"] == 56

    verified_settlements = [b for b in result["batch_verdicts"] if b.status == "verified"]
    assert len(verified_settlements) == 4  # SETTL_001 through SETTL_004

    assert len(result["unrelated_bank_lines"]) == 2  # rent + AWS
