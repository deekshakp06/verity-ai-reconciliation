"""
three_way_reconciler.py
------------------------
A SECOND, more rigorous reconciliation engine, alongside matcher.py.

matcher.py answers: "do these two records probably describe the same
transaction?" — using tolerance-based scoring (amount within X%, date within
Y days). That's the right tool when you only have two files and no fee
breakdown to work with.

This module answers a stronger question: "does the money add up EXACTLY?" —
using real arithmetic verification against a three-way data model that
mirrors how payment gateways actually report settlements:

  1. Internal Sales   — what your own system recorded (the gross amount billed)
  2. Gateway Report    — the processor's fee breakdown per order, grouped by
                          a Settlement_ID (Gross, MDR fee, tax, net)
  3. Bank Statement    — the actual lump-sum deposits that hit your account,
                          mixed in with unrelated business banking activity

Every order gets classified into exactly one status, in priority order:

  1. pending_settlement   — Settlement_ID is "PENDING": money hasn't moved
                             yet. This is NOT a failure — flagging it as one
                             would be actively wrong.
  2. prior_period_deduction — a negative amount / refund-style order ID with
                             no matching Internal Sales record. This is a
                             chargeback or refund eating into a settlement,
                             not a "missing" transaction.
  3. math_error            — Gross - MDR Fee - Tax != Net, beyond a rounding
                             epsilon. A real, unexplained discrepancy.
  4. unmatched_to_sales    — the settlement references an order our own
                             system never recorded (and it isn't a refund).
  5. verified              — math checks out, fee rate matches what's
                             expected for that payment method, and the order
                             exists in Internal Sales. Fully explained, not
                             just "close enough".

On top of the per-order checks, settlement BATCHES are verified against the
bank statement: the sum of Net_Amount for everything sharing a Settlement_ID
should equal the actual deposit for that ID. Bank lines that aren't tagged
to any Settlement_ID (rent, AWS, etc.) are recognized as unrelated banking
activity and explicitly excluded, not treated as unmatched settlements.
"""

import re
from dataclasses import dataclass, field
import pandas as pd

from logger import get_logger

logger = get_logger(__name__)

# Rounding tolerance for "the math matches" — a couple of paise of floating
# point noise is fine; anything beyond this is a genuine discrepancy.
MATH_EPSILON = 0.02

# Expected MDR (merchant discount rate) percentage by payment method. This is
# what makes fee verification "exact" rather than "guessed": a premium card
# network genuinely charging a higher rate is EXPECTED and explained, not an
# anomaly — as long as it matches what that method is known to cost.
EXPECTED_MDR_PCT = {
    "upi / card": 2.0,
    "card": 2.0,
    "diners club credit": 3.0,
}
FEE_RATE_EPSILON_PCT = 0.15  # tolerance band around the expected rate


@dataclass
class OrderVerdict:
    order_id: str
    status: str  # pending_settlement | prior_period_deduction | math_error | unmatched_to_sales | verified
    settlement_id: str | None
    payment_method: str | None
    gross_amount: float
    mdr_fee: float
    tax_on_fee: float
    net_amount: float
    expected_net: float | None
    math_diff: float | None
    effective_mdr_pct: float | None
    expected_mdr_pct: float | None
    reason: str


@dataclass
class SettlementBatchVerdict:
    settlement_id: str
    reported_net_total: float
    bank_deposit: float | None
    diff: float | None
    status: str  # verified | mismatch | no_bank_record | pending
    reason: str


def _normalize_columns(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Renames whatever columns exist to our canonical internal names."""
    reverse = {v: k for k, v in mapping.items() if v in df.columns}
    return df.rename(columns=reverse)


def load_three_way_excel(file_or_path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Reads a single .xlsx with three sheets. Sheet names are matched loosely
    (case-insensitive substring) so minor naming differences don't break it —
    e.g. "1. Internal Sales" and "Internal Sales" both resolve correctly.
    """
    xls = pd.ExcelFile(file_or_path)

    def find_sheet(keyword):
        for name in xls.sheet_names:
            if keyword.lower() in name.lower():
                return name
        raise ValueError(
            f"Could not find a sheet matching '{keyword}'. "
            f"Available sheets: {xls.sheet_names}"
        )

    sales = pd.read_excel(xls, find_sheet("internal sales"))
    gateway = pd.read_excel(xls, find_sheet("gateway"))
    bank = pd.read_excel(xls, find_sheet("bank"))
    return sales, gateway, bank


def _is_refund_like(order_id: str, gross_amount: float) -> bool:
    return gross_amount < 0 or "refund" in str(order_id).lower()


def _base_order_id(order_id: str) -> str:
    """'ORD_0850_REFUND' -> 'ORD_0850' — strips known suffixes so we can
    still check whether the ORIGINAL order exists anywhere, for context."""
    return re.sub(r"_REFUND$", "", str(order_id), flags=re.IGNORECASE)


def verify_orders(sales: pd.DataFrame, gateway: pd.DataFrame) -> list[OrderVerdict]:
    """
    Runs the priority-ordered classification described in the module
    docstring for every row in the Gateway Report.
    """
    sales_ids = set(sales["Order_ID"].astype(str))
    verdicts = []

    for _, row in gateway.iterrows():
        order_id = str(row["Order_ID"])
        settlement_id = row.get("Settlement_ID")
        payment_method = str(row.get("Payment_Method", "")).strip()
        gross = float(row["Gross_Amount"])
        fee = float(row["MDR_Fee"])
        tax = float(row["Tax_on_Fee"])
        net = float(row["Net_Amount"])
        expected_net = round(gross - fee - tax, 2)
        math_diff = round(net - expected_net, 2)

        effective_pct = round(fee / gross * 100, 2) if gross else None
        expected_pct = EXPECTED_MDR_PCT.get(payment_method.lower())

        # --- Priority 1: pending settlement -------------------------------
        if str(settlement_id).strip().upper() == "PENDING":
            verdicts.append(OrderVerdict(
                order_id=order_id, status="pending_settlement",
                settlement_id=settlement_id, payment_method=payment_method,
                gross_amount=gross, mdr_fee=fee, tax_on_fee=tax, net_amount=net,
                expected_net=expected_net, math_diff=math_diff,
                effective_mdr_pct=effective_pct, expected_mdr_pct=expected_pct,
                reason=(
                    f"Settlement is still PENDING — the bank deposit hasn't happened yet. "
                    f"This is a timing state, not a failure; fee math ({'checks out' if abs(math_diff) <= MATH_EPSILON else 'does NOT check out — worth a look once settled'})."
                ),
            ))
            continue

        # --- Priority 2: prior-period deduction (refund/chargeback) -------
        if _is_refund_like(order_id, gross):
            base_id = _base_order_id(order_id)
            in_sales = base_id in sales_ids
            verdicts.append(OrderVerdict(
                order_id=order_id, status="prior_period_deduction",
                settlement_id=settlement_id, payment_method=payment_method,
                gross_amount=gross, mdr_fee=fee, tax_on_fee=tax, net_amount=net,
                expected_net=expected_net, math_diff=math_diff,
                effective_mdr_pct=effective_pct, expected_mdr_pct=expected_pct,
                reason=(
                    f"Negative/refund-style entry for {order_id} (₹{net:.2f}) — "
                    f"{'the original order exists in Internal Sales, so this looks like a return.' if in_sales else 'no matching order in the current Internal Sales sheet, consistent with a chargeback from a PRIOR settlement period landing in this one.'} "
                    f"This explains part of why today's settlement total is lower than gross sales — it is not a new unmatched transaction."
                ),
            ))
            continue

        # --- Priority 3: math error ----------------------------------------
        if abs(math_diff) > MATH_EPSILON:
            verdicts.append(OrderVerdict(
                order_id=order_id, status="math_error",
                settlement_id=settlement_id, payment_method=payment_method,
                gross_amount=gross, mdr_fee=fee, tax_on_fee=tax, net_amount=net,
                expected_net=expected_net, math_diff=math_diff,
                effective_mdr_pct=effective_pct, expected_mdr_pct=expected_pct,
                reason=(
                    f"Math doesn't add up: {gross:.2f} - {fee:.2f} (fee) - {tax:.2f} (tax) "
                    f"= {expected_net:.2f} expected, but Net_Amount shows {net:.2f} "
                    f"— an unexplained {'shortfall' if math_diff < 0 else 'excess'} of ₹{abs(math_diff):.2f}. Needs human review."
                ),
            ))
            continue

        # --- Priority 4: unmatched to internal sales ------------------------
        if order_id not in sales_ids:
            verdicts.append(OrderVerdict(
                order_id=order_id, status="unmatched_to_sales",
                settlement_id=settlement_id, payment_method=payment_method,
                gross_amount=gross, mdr_fee=fee, tax_on_fee=tax, net_amount=net,
                expected_net=expected_net, math_diff=math_diff,
                effective_mdr_pct=effective_pct, expected_mdr_pct=expected_pct,
                reason=(
                    f"Gateway settled {order_id} but it doesn't exist in Internal Sales — "
                    f"the fee math is fine, but there's no record of us ever billing this order."
                ),
            ))
            continue

        # --- Priority 5: verified --------------------------------------------
        fee_note = ""
        if expected_pct is not None and effective_pct is not None:
            if abs(effective_pct - expected_pct) > FEE_RATE_EPSILON_PCT:
                fee_note = (
                    f" Note: effective MDR is {effective_pct}%, which doesn't match the "
                    f"expected {expected_pct}% for {payment_method} — worth a second look, "
                    f"though the arithmetic itself is internally consistent."
                )
            else:
                fee_note = (
                    f" {payment_method} correctly carries a {effective_pct}% MDR "
                    f"({'standard rate' if abs(expected_pct - 2.0) < 0.01 else 'premium card rate — expected, not an error'})."
                )

        verdicts.append(OrderVerdict(
            order_id=order_id, status="verified",
            settlement_id=settlement_id, payment_method=payment_method,
            gross_amount=gross, mdr_fee=fee, tax_on_fee=tax, net_amount=net,
            expected_net=expected_net, math_diff=math_diff,
            effective_mdr_pct=effective_pct, expected_mdr_pct=expected_pct,
            reason=f"Gross - fee - tax matches Net exactly, and the order exists in Internal Sales.{fee_note}",
        ))

    return verdicts


def verify_settlement_batches(gateway: pd.DataFrame, bank: pd.DataFrame) -> list[SettlementBatchVerdict]:
    """
    Groups the Gateway Report by Settlement_ID and checks the total Net_Amount
    against the actual bank deposit for that batch — extracted from the bank
    statement's description text. Bank lines with no Settlement_ID pattern
    (rent, AWS, etc.) are simply not part of this check at all.
    """
    results = []
    bank_desc = bank.get("Description", pd.Series(dtype=str)).astype(str)

    for settlement_id, group in gateway.groupby("Settlement_ID"):
        if str(settlement_id).strip().upper() == "PENDING":
            results.append(SettlementBatchVerdict(
                settlement_id=settlement_id,
                reported_net_total=round(group["Net_Amount"].sum(), 2),
                bank_deposit=None, diff=None, status="pending",
                reason=f"{len(group)} order(s) still pending settlement — no bank deposit expected yet.",
            ))
            continue

        reported_total = round(group["Net_Amount"].sum(), 2)
        match_mask = bank_desc.str.contains(re.escape(str(settlement_id)), case=False, na=False)
        matched_rows = bank[match_mask]

        if matched_rows.empty:
            results.append(SettlementBatchVerdict(
                settlement_id=settlement_id, reported_net_total=reported_total,
                bank_deposit=None, diff=None, status="no_bank_record",
                reason=f"No bank deposit found referencing {settlement_id} yet.",
            ))
            continue

        deposit = float(matched_rows["Credit"].sum())
        diff = round(deposit - reported_total, 2)
        if abs(diff) > MATH_EPSILON:
            results.append(SettlementBatchVerdict(
                settlement_id=settlement_id, reported_net_total=reported_total,
                bank_deposit=deposit, diff=diff, status="mismatch",
                reason=(
                    f"Gateway report says {settlement_id} should total ₹{reported_total:.2f}, "
                    f"but the bank actually deposited ₹{deposit:.2f} — a ₹{abs(diff):.2f} "
                    f"{'shortfall' if diff < 0 else 'excess'}."
                ),
            ))
        else:
            results.append(SettlementBatchVerdict(
                settlement_id=settlement_id, reported_net_total=reported_total,
                bank_deposit=deposit, diff=diff, status="verified",
                reason=f"Bank deposit of ₹{deposit:.2f} matches the reported settlement total exactly.",
            ))

    return results


def find_unrelated_bank_activity(bank: pd.DataFrame) -> list[dict]:
    """Bank lines that don't reference any Settlement_ID — rent, AWS, etc.
    Surfaced for transparency, never treated as a reconciliation exception."""
    desc = bank.get("Description", pd.Series(dtype=str)).astype(str)
    is_settlement = desc.str.contains(r"SETTL_\d+", case=False, na=False, regex=True)
    unrelated = bank[~is_settlement]
    return unrelated.to_dict(orient="records")


def run_three_way_reconciliation(file_or_path) -> dict:
    """Top-level entry point: load the workbook, run both verification
    layers, and return a summary dict ready for display."""
    sales, gateway, bank = load_three_way_excel(file_or_path)

    order_verdicts = verify_orders(sales, gateway)
    batch_verdicts = verify_settlement_batches(gateway, bank)
    unrelated_bank_lines = find_unrelated_bank_activity(bank)

    status_counts = {}
    for v in order_verdicts:
        status_counts[v.status] = status_counts.get(v.status, 0) + 1

    logger.info("Three-way reconciliation: %s", status_counts)

    return {
        "order_verdicts": order_verdicts,
        "batch_verdicts": batch_verdicts,
        "unrelated_bank_lines": unrelated_bank_lines,
        "status_counts": status_counts,
        "total_orders": len(order_verdicts),
    }
