"""
explainer.py
------------
Turns a raw exception (two records that didn't confidently match) into a
plain-English explanation a non-technical finance-ops person could act on
immediately, plus a suggested next step.

Falls back to a template-based explanation if no LLM key is set, so the
pipeline always produces something useful.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger import get_logger

logger = get_logger(__name__)

# How many explanation requests to run at once when an LLM is connected.
# Each call is network-bound (waiting on the API), so a modest thread pool
# gives a large speedup with no risk of CPU contention. Kept conservative to
# stay well under Groq's free-tier rate limits even on a large exception batch.
MAX_PARALLEL_EXPLANATIONS = 6


def _template_explanation(decision) -> str:
    """Simple, still-useful explanation with zero API cost."""
    if decision.settlement_ref is None:
        return (
            f"Payment {decision.payment_id} has no matching settlement record at all. "
            f"Suggested next step: check if this payment is still pending settlement, "
            f"or was reversed/refunded before settling."
        )
    return (
        f"Payment {decision.payment_id} was compared against settlement "
        f"{decision.settlement_ref} but only reached {decision.confidence:.0f}% confidence. "
        f"Reason: {decision.reason} "
        f"Suggested next step: a human should manually confirm this pair before closing it out."
    )


def explain_exception(decision) -> str:
    from llm_client import get_client_and_model

    client, model = get_client_and_model()
    if client is None:
        return _template_explanation(decision)

    try:
        prompt = f"""A payment reconciliation agent found a possible mismatch.
Payment ID: {decision.payment_id}
Candidate settlement reference: {decision.settlement_ref}
Confidence score: {decision.confidence}
Raw reason: {decision.reason}
Amount A (payment): {decision.amount_a}, Amount B (settlement): {decision.amount_b}
Date A (payment): {decision.date_a}, Date B (settlement): {decision.date_b}

Write a short (2-3 sentence), plain-English explanation for a finance-ops
person, including one concrete suggested next step. No jargon."""

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("LLM explanation failed for %s, using template: %s", decision.payment_id, e)
        return _template_explanation(decision)


def explain_all_exceptions(exceptions) -> dict:
    """
    Returns {payment_id: explanation_text} for every exception.

    When an LLM is connected, requests run concurrently (bounded thread pool)
    instead of one at a time — this was previously the dominant cost in a
    full reconciliation run (e.g. 30 exceptions x ~0.5s each sequentially
    versus the same 30 calls spread across parallel workers).

    Falls back to plain sequential template generation with no LLM connected,
    since there's no network latency to parallelize away in that case.
    """
    if not exceptions:
        return {}

    from llm_client import get_client_and_model

    client, _ = get_client_and_model()
    if client is None:
        # No network calls involved — sequential is already instant.
        return {d.payment_id: _template_explanation(d) for d in exceptions}

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_EXPLANATIONS) as pool:
        future_to_decision = {pool.submit(explain_exception, d): d for d in exceptions}
        for future in as_completed(future_to_decision):
            decision = future_to_decision[future]
            try:
                results[decision.payment_id] = future.result()
            except Exception as e:
                logger.warning("Parallel explanation failed for %s: %s", decision.payment_id, e)
                results[decision.payment_id] = _template_explanation(decision)

    return results


# ---------------------------------------------------------------------------
# Exact-verification exceptions — a genuinely different shape of record
# (plain dicts from auto_reconciler.py: order_id, status, gross, fee, tax,
# net, expected_net, diff — not the matcher.py Decision objects above), so
# they need their own template + prompt rather than reusing explain_exception.
# Without this, "explanation" was silently just a copy of "reason" for every
# exact-verification exception — this closes that gap so "Explain" in
# "Match. Flag. Explain." is genuinely true on both engines, not just one.
# ---------------------------------------------------------------------------

def _template_explanation_exact(order: dict) -> str:
    status = order.get("status")
    if status == "math_error":
        return (
            f"Order {order['order_id']}'s settlement math doesn't add up: expected "
            f"₹{order.get('expected_net')} after fees, but ₹{order.get('net')} was reported "
            f"— a ₹{abs(order.get('diff', 0)):.2f} discrepancy. Suggested next step: "
            f"check this order's fee breakdown with the payment gateway directly."
        )
    if status == "unmatched_to_reference":
        return (
            f"Order {order['order_id']} was settled by the gateway but has no matching "
            f"record in our own system. Suggested next step: check for a manual or "
            f"offline sale that may have bypassed normal order entry."
        )
    if status == "prior_period_deduction":
        return (
            f"Order {order['order_id']} looks like a refund or chargeback from a prior "
            f"period, not a new transaction. Suggested next step: confirm against last "
            f"month's records before treating it as an error."
        )
    return f"Order {order['order_id']} needs a manual look: {order.get('reason', '')}"


def explain_exact_exception(order: dict) -> str:
    from llm_client import get_client_and_model

    client, model = get_client_and_model()
    if client is None:
        return _template_explanation_exact(order)

    try:
        prompt = f"""An exact-arithmetic reconciliation engine flagged a settlement order.
Order ID: {order['order_id']}
Status: {order.get('status')}
Gross: {order.get('gross')}, Fee: {order.get('fee')}, Tax: {order.get('tax')}, Net: {order.get('net')}
Expected net (gross - fee - tax): {order.get('expected_net')}
Difference: {order.get('diff')}
Raw computed reason: {order.get('reason')}

Write a short (2-3 sentence), plain-English explanation for a finance-ops
person, including one concrete suggested next step. No jargon. Do not just
repeat the raw reason verbatim — add context on why this matters and what
to actually do about it."""

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("LLM explanation failed for %s, using template: %s", order.get("order_id"), e)
        return _template_explanation_exact(order)


def explain_all_exact_exceptions(orders: list[dict]) -> dict:
    """Returns {order_id: explanation_text} for every non-verified order
    from the exact-verification engine. Same parallel-dispatch pattern as
    explain_all_exceptions, adapted for dict-shaped records."""
    exceptions = [o for o in orders if o.get("status") != "verified"]
    if not exceptions:
        return {}

    from llm_client import get_client_and_model

    client, _ = get_client_and_model()
    if client is None:
        return {o["order_id"]: _template_explanation_exact(o) for o in exceptions}

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_EXPLANATIONS) as pool:
        future_to_order = {pool.submit(explain_exact_exception, o): o for o in exceptions}
        for future in as_completed(future_to_order):
            order = future_to_order[future]
            try:
                results[order["order_id"]] = future.result()
            except Exception as e:
                logger.warning("Parallel explanation failed for %s: %s", order["order_id"], e)
                results[order["order_id"]] = _template_explanation_exact(order)

    return results
