"""
test_explainer_exact.py
--------------------------
Tests the exact-verification explanation path — this exists specifically
because "explanation" was silently just a copy of "reason" for every
exact-verification exception before this was fixed. These tests guard
against that regression.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from explainer import explain_all_exact_exceptions, _template_explanation_exact


def make_math_error_order():
    return {
        "order_id": "order_00000005", "status": "math_error",
        "reason": "26886.92 - 537.74 (fee) - 96.79 (tax) = 26252.39 expected, but reported net is 26210.39 — an unexplained shortfall of ₹42.00.",
        "gross": 26886.92, "fee": 537.74, "tax": 96.79, "net": 26210.39,
        "expected_net": 26252.39, "diff": -42.00,
    }


def make_unmatched_order():
    return {
        "order_id": "order_00000099", "status": "unmatched_to_reference",
        "reason": "Fee breakdown settles order_00000099 but it has no matching record in the reference source.",
        "gross": 8000.0, "fee": 160.0, "tax": 28.8, "net": 7811.2,
        "expected_net": 7811.2, "diff": 0.0,
    }


def test_template_explanation_is_distinct_from_raw_reason():
    """The whole point of this fix: explanation must NOT be identical to reason."""
    order = make_math_error_order()
    explanation = _template_explanation_exact(order)
    assert explanation != order["reason"]
    assert order["order_id"] in explanation


def test_explain_all_exact_exceptions_only_covers_non_verified_orders():
    orders = [
        make_math_error_order(),
        {"order_id": "order_1", "status": "verified", "reason": "fine", "gross": 1, "fee": 0, "tax": 0, "net": 1, "expected_net": 1, "diff": 0},
    ]
    explanations = explain_all_exact_exceptions(orders)
    assert "order_00000005" in explanations
    assert "order_1" not in explanations  # verified orders don't need an explanation


def test_explain_all_exact_exceptions_handles_empty_list():
    assert explain_all_exact_exceptions([]) == {}


def test_math_error_and_unmatched_get_different_template_explanations():
    """Different failure modes should produce genuinely different guidance,
    not the same generic text regardless of what actually went wrong."""
    math_error_explanation = _template_explanation_exact(make_math_error_order())
    unmatched_explanation = _template_explanation_exact(make_unmatched_order())
    assert math_error_explanation != unmatched_explanation
    assert "fee breakdown" in math_error_explanation.lower() or "discrepancy" in math_error_explanation.lower()
    assert "manual" in unmatched_explanation.lower() or "offline" in unmatched_explanation.lower()
