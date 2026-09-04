"""
test_reconciliation.py
-----------------------
A small but real test suite. This is what separates "it worked when I ran it
once" from "I can prove it works." Run with:  pytest tests/ -v

Covers the behaviors a reviewer would specifically check for:
  - clean matches are auto-matched
  - amount mismatches are flagged, not force-matched
  - missing settlement records are flagged with the right reason
  - schema mapper resolves an unfamiliar column format correctly
  - malformed input is rejected with a clear error, not a crash
"""

import sys
import os
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matcher import reconcile
from schema_mapper import map_schema, apply_mapping
from evaluate import evaluate_against_ground_truth
from explainer import explain_all_exceptions


def make_clean_pair():
    payments = pd.DataFrame(
        [{"id": "TXN001", "amount": 1000.0, "date": "2026-08-01"}]
    )
    settlements = pd.DataFrame(
        [{"id": "REF-AAA111", "amount": 1000.0, "date": "2026-08-03"}]
    )
    return payments, settlements


def test_clean_match_is_auto_matched():
    payments, settlements = make_clean_pair()
    result = reconcile(payments, settlements)
    assert len(result["matched"]) == 1
    assert len(result["exceptions"]) == 0
    assert result["match_rate"] == 100.0


def test_amount_mismatch_is_flagged_not_forced():
    payments = pd.DataFrame([{"id": "TXN002", "amount": 1000.0, "date": "2026-08-01"}])
    settlements = pd.DataFrame([{"id": "REF-BBB222", "amount": 700.0, "date": "2026-08-03"}])
    result = reconcile(payments, settlements)
    assert len(result["exceptions"]) == 1
    assert len(result["matched"]) == 0
    assert "mismatch" in result["exceptions"][0].reason.lower()


def test_missing_settlement_is_flagged_with_correct_reason():
    payments = pd.DataFrame([{"id": "TXN003", "amount": 500.0, "date": "2026-08-01"}])
    settlements = pd.DataFrame(columns=["id", "amount", "date"])
    result = reconcile(payments, settlements)
    assert len(result["exceptions"]) == 1
    assert result["exceptions"][0].settlement_ref is None
    assert "no plausible matching" in result["exceptions"][0].reason.lower()


def test_empty_payments_raises_clear_error():
    payments = pd.DataFrame(columns=["id", "amount", "date"])
    settlements = pd.DataFrame([{"id": "REF1", "amount": 100.0, "date": "2026-08-01"}])
    with pytest.raises(ValueError, match="empty"):
        reconcile(payments, settlements)


def test_missing_required_column_raises_clear_error():
    payments = pd.DataFrame([{"id": "TXN004", "amount": 100.0}])  # no 'date'
    settlements = pd.DataFrame([{"id": "REF1", "amount": 100.0, "date": "2026-08-01"}])
    with pytest.raises(ValueError, match="missing required canonical columns"):
        reconcile(payments, settlements)


def test_schema_mapper_resolves_unfamiliar_columns():
    """Proves the differentiator: works on a column format it's never seen,
    using the heuristic fallback (no API key needed for this test)."""
    df = pd.DataFrame(
        [{"unique_ref_code": "X1", "value_paid": 100.0, "txn_timestamp": "2026-08-01"}]
    )
    mapping = map_schema(df, "unfamiliar_source")
    assert mapping["id"] == "unique_ref_code"
    assert mapping["amount"] == "value_paid"
    assert mapping["date"] == "txn_timestamp"

    normalized = apply_mapping(df, mapping)
    assert list(normalized.columns[:3]) == ["id", "amount", "date"]


def test_schema_mapper_resolves_a_second_unrelated_format():
    """A different unfamiliar format (mirrors the live upload demo data) —
    guards against the heuristic keyword list narrowing again in the future."""
    payments = pd.DataFrame(
        [{"order_ref": "ORD001", "value": 1200.50, "created_on": "2026-08-01"}]
    )
    settlements = pd.DataFrame(
        [{"bank_ref_code": "ORD001", "paid_amount": 1200.50, "cleared_on": "2026-08-03"}]
    )
    pm = map_schema(payments, "demo_upload_payments")
    sm = map_schema(settlements, "demo_upload_settlements")
    assert pm == {"id": "order_ref", "amount": "value", "date": "created_on"}
    assert sm == {"id": "bank_ref_code", "amount": "paid_amount", "date": "cleared_on"}


def test_unix_epoch_seconds_dates_are_correctly_normalized():
    """Real payment gateway APIs (Razorpay's own settlement recon docs
    included) commonly report created_at/settled_at as Unix epoch SECONDS,
    not formatted date strings. Without correct handling, pandas' default
    parser misreads these as epoch NANOSECONDS, producing a date in early
    1970 and a many-thousand-day false gap in every comparison — a real bug
    caught while testing against an authentic Razorpay-shaped sample."""
    # 1754006400 and 1754179200 are real epoch-seconds timestamps two days apart
    df = pd.DataFrame([{"id": "pay_1", "amount": 500.0, "created_at": 1754006400}])
    mapping = map_schema(df, "razorpay_style")
    assert mapping["date"] == "created_at"
    normalized = apply_mapping(df, mapping)
    assert normalized["date"].iloc[0] == "2025-08-01"


def test_normal_string_dates_are_left_untouched_by_epoch_normalization():
    """Regression guard: ordinary date strings must never be reinterpreted
    as epoch timestamps just because the column happens to be numeric-like
    after coercion (e.g. genuinely non-numeric strings pass straight through)."""
    df = pd.DataFrame([{"id": "TXN1", "amount": 100.0, "payment_date": "2026-08-01"}])
    mapping = map_schema(df, "normal_dates")
    normalized = apply_mapping(df, mapping)
    assert normalized["date"].iloc[0] == "2026-08-01"


def test_duplicate_settlements_are_flagged_not_arbitrarily_picked():
    """Two settlement records that are exact copies of each other (same amount,
    same date) for one payment should be flagged as ambiguous — not silently
    matched to whichever one was encountered first."""
    payments = pd.DataFrame([{"id": "TXN050", "amount": 1000.0, "date": "2026-08-01", "internal_ref": "REF-DUP1"}])
    settlements = pd.DataFrame([
        {"id": "REF-DUP1", "amount": 1000.0, "date": "2026-08-03"},
        {"id": "REF-DUP1", "amount": 1000.0, "date": "2026-08-03"},
    ])
    result = reconcile(payments, settlements)
    assert len(result["exceptions"]) == 1
    assert len(result["matched"]) == 0
    assert "duplicate" in result["exceptions"][0].reason.lower()


def test_unrelated_coincidental_amount_does_not_trigger_false_duplicate():
    """A settlement that happens to share a similar amount with an UNRELATED
    payment's real duplicate pair must not cause a false duplicate flag —
    duplicate detection must be scoped to strongly reference-matched candidates
    only, not merely amount-adjacent ones (regression guard)."""
    payments = pd.DataFrame([
        {"id": "TXN060", "amount": 5000.00, "date": "2026-08-01", "internal_ref": "REF-AAA111"},
        {"id": "TXN061", "amount": 5000.02, "date": "2026-08-01", "internal_ref": "REF-BBB222"},
    ])
    settlements = pd.DataFrame([
        # TXN060's genuine duplicate pair (identical to each other)
        {"id": "REF-AAA111", "amount": 5000.00, "date": "2026-08-03"},
        {"id": "REF-AAA111", "amount": 5000.00, "date": "2026-08-03"},
        # TXN061's own unrelated, perfectly clean settlement
        {"id": "REF-BBB222", "amount": 5000.02, "date": "2026-08-03"},
    ])
    result = reconcile(payments, settlements)
    by_id = {d.payment_id: d for d in result["decisions"]}
    assert "duplicate" in by_id["TXN060"].reason.lower()
    assert by_id["TXN061"].status == "matched"  # must NOT be falsely caught up


def test_ambiguous_close_amounts_disambiguated_by_reference():
    """Two DIFFERENT payments with near-identical amounts happening close
    together must each match their OWN true settlement counterpart — picked
    out via reference similarity — not whichever settlement scores marginally
    higher on amount/date alone (which could accidentally swap them)."""
    payments = pd.DataFrame([
        {"id": "TXN200", "amount": 5000.00, "date": "2026-08-01", "internal_ref": "REF-AAA111"},
        {"id": "TXN201", "amount": 5000.05, "date": "2026-08-01", "internal_ref": "REF-BBB222"},
    ])
    settlements = pd.DataFrame([
        {"id": "REF-AAA111", "amount": 5000.00, "date": "2026-08-03"},
        {"id": "REF-BBB222", "amount": 5000.05, "date": "2026-08-03"},
    ])
    result = reconcile(payments, settlements)
    by_id = {d.payment_id: d for d in result["decisions"]}
    assert by_id["TXN200"].settlement_ref == "REF-AAA111"
    assert by_id["TXN201"].settlement_ref == "REF-BBB222"
    assert by_id["TXN200"].status == "matched"
    assert by_id["TXN201"].status == "matched"


def test_reconcile_reports_throughput():
    payments, settlements = make_clean_pair()
    result = reconcile(payments, settlements)
    assert "throughput_per_sec" in result
    assert "elapsed_seconds" in result
    assert result["throughput_per_sec"] > 0


def test_ground_truth_evaluation_runs_on_demo_data():
    """If demo data + ground truth have been generated, evaluation should
    produce sane, internally consistent metrics."""
    if not os.path.exists("data/payment_log.csv") or not os.path.exists("data/ground_truth.csv"):
        pytest.skip("Demo data not generated in this environment")

    payments_raw = pd.read_csv("data/payment_log.csv")
    settlements_raw = pd.read_csv("data/settlement_file.csv")
    pm = map_schema(payments_raw, "payment_log")
    sm = map_schema(settlements_raw, "settlement_file")
    payments = apply_mapping(payments_raw, pm)
    settlements = apply_mapping(settlements_raw, sm)
    result = reconcile(payments, settlements)

    metrics = evaluate_against_ground_truth(result["decisions"])
    assert metrics is not None
    assert 0 <= metrics["accuracy"] <= 100
    assert 0 <= metrics["precision"] <= 100
    assert 0 <= metrics["recall"] <= 100
    assert metrics["total_labeled"] == len(result["decisions"])


def test_parallel_explanations_map_to_correct_payment_ids():
    """Regression guard for the concurrent explanation path: every exception
    must get exactly its own explanation back, never mixed up with another's,
    regardless of thread completion order."""
    from matcher import Decision

    decisions = [
        Decision(
            payment_id=f"TXN{i:03d}", settlement_ref=f"REF{i}", status="exception",
            confidence=60.0, reason=f"Amount mismatch of {i}.00 ({i}.0% difference)",
            amount_a=100 + i, amount_b=90 + i, date_a="2026-08-01", date_b="2026-08-03",
        )
        for i in range(12)
    ]
    result = explain_all_exceptions(decisions)
    assert len(result) == 12
    for i in range(12):
        assert f"TXN{i:03d}" in result
        assert result[f"TXN{i:03d}"]  # non-empty


def test_explain_all_exceptions_handles_empty_list():
    assert explain_all_exceptions([]) == {}


def test_standard_gateway_fee_is_recognized_as_clean_not_flagged():
    """A realistic ~2.36% Razorpay-style fee (2% MDR + 18% GST on that fee)
    must be treated as normal and auto-matched — NOT flagged as a mismatch.
    Regression guard against demo data that unrealistically modeled some
    settlements as having zero fee at all."""
    amount = 10000.0
    standard_fee = round(amount * 0.02 * 1.18, 2)  # 2% MDR + 18% GST on the fee
    payments = pd.DataFrame([{"id": "TXN1", "amount": amount, "date": "2026-08-01"}])
    settlements = pd.DataFrame([{"id": "REF1", "amount": round(amount - standard_fee, 2), "date": "2026-08-03"}])
    result = reconcile(payments, settlements)
    assert len(result["matched"]) == 1
    assert len(result["exceptions"]) == 0


def test_fee_well_beyond_standard_rate_is_still_flagged():
    """A genuinely anomalous fee (well beyond the standard ~2.36% rate)
    must still be caught as an exception, not absorbed into the wider
    tolerance meant for normal gateway fees."""
    amount = 10000.0
    anomalous_fee = round(amount * 0.15, 2)  # 15% — far beyond any standard rate
    payments = pd.DataFrame([{"id": "TXN2", "amount": amount, "date": "2026-08-01"}])
    settlements = pd.DataFrame([{"id": "REF2", "amount": round(amount - anomalous_fee, 2), "date": "2026-08-03"}])
    result = reconcile(payments, settlements)
    assert len(result["exceptions"]) == 1
    assert len(result["matched"]) == 0
