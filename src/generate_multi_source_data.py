"""
generate_multi_source_data.py
-------------------------------
Generates the THREE-FILE demo dataset that is now Verity's default,
zero-upload experience — because "Multi-source reconciliation" is one of
only four example directions explicitly named on the buildathon page, this
is the story every panelist sees first, not something they have to know to
upload a third file to discover.

Three files, using REAL field names pulled from each company's own public
docs (not invented for this demo):

1. sample_internal_orders.csv              — our own order records
2. sample_razorpay_real_schema_settlement.csv — Razorpay's real settlement
   recon report shape: entity_id, type, fee, tax, settlement_id, order_id,
   method, etc. (see README §6b for the source)
3. sample_razorpay_bank_statement.csv      — a real Indian bank statement
   shape: narration text + credited amount, with unrelated banking noise
   mixed in (rent, a cloud bill) exactly like a real statement always has

DELIBERATELY PLANTED ISSUES — a demo where everything is perfectly clean
proves nothing (the buildathon's own words). This generator plants exactly
three realistic problems, mirroring generate_data.py's approach for the
2-file engine:

  1. A MATH ERROR   — one order's settlement math doesn't add up
  2. An UNMATCHED SETTLEMENT — the gateway settled an order our internal
     system never recorded (e.g. a manual/offline sale bypassing the system)
  3. A BATCH MISMATCH — one settlement batch's bank deposit doesn't match
     the total the gateway report says it should be

Everything else verifies cleanly, so the overall picture stays realistic —
mostly healthy, with a small number of genuine, specific problems, exactly
like a real settlement day.
"""

import pandas as pd
import random

random.seed(7)

NUM_CLEAN_ORDERS = 55  # + planted-issue rows below = 60+ total, meeting the
                        # buildathon's explicit "50+ record batch" requirement
STANDARD_MDR_PCT = 2.0
GST_ON_FEE_PCT = 18.0


def standard_fee(amount: float) -> float:
    mdr = round(amount * (STANDARD_MDR_PCT / 100), 2)
    gst = round(mdr * (GST_ON_FEE_PCT / 100), 2)
    return round(mdr + gst, 2)


def generate():
    order_rows = []
    settlement_rows = []
    amounts = [round(random.uniform(200, 50000), 2) for _ in range(NUM_CLEAN_ORDERS)]
    methods = ["upi", "card", "netbanking"]

    # --- Clean, correctly-verifiable orders (the healthy majority) ---------
    for i, amount in enumerate(amounts, start=1):
        order_id = f"order_{i:08d}"
        payment_id = f"pay_{i:010d}"
        fee = standard_fee(amount)
        net = round(amount - fee, 2)
        mdr = round(amount * (STANDARD_MDR_PCT / 100), 2)
        tax = round(fee - mdr, 2)
        settlement_id = f"setl_{((i - 1) // 6) + 1:06d}"  # groups of ~6 orders per batch
        order_day = (i % 28) + 1
        settled_day = min(order_day + 2, 28)  # realistic ~2-day settlement lag

        order_rows.append({
            "internal_order_ref": order_id, "billed_amount": amount,
            "order_created_on": f"2026-08-{order_day:02d}",
        })
        settlement_rows.append({
            "entity_id": payment_id, "type": "payment", "debit": 0, "credit": net,
            "amount": amount, "currency": "INR", "fee": mdr, "tax": tax,
            "settled": True, "settlement_id": settlement_id,
            "settlement_utr": f"{1000000000 + i}", "order_id": order_id,
            "method": random.choice(methods), "description": f"Order {i}",
            "created_at": f"2026-08-{settled_day:02d}",
        })

    # --- Planted issue 1: genuine math errors (3 orders) ---------------------
    # Real, unexplained shortfalls in the settlement math — not guesses.
    math_error_targets = {
        "order_00000005": 42.00,
        "order_00000018": 15.50,
        "order_00000037": 88.25,
    }
    for row in settlement_rows:
        if row["order_id"] in math_error_targets:
            row["credit"] = round(row["credit"] - math_error_targets[row["order_id"]], 2)

    # --- Planted issue 2: settled but never recorded internally (2 orders) ---
    # The gateway processed and settled these orders, but our own system has
    # no record of them — e.g. manual/offline sales that bypassed the system.
    ghost_orders = [
        ("order_00000099", "pay_0000000099", 8000.00, "setl_000003"),
        ("order_00000098", "pay_0000000098", 3250.00, "setl_000006"),
    ]
    for order_id, payment_id, ghost_amount, settlement_id in ghost_orders:
        ghost_fee = standard_fee(ghost_amount)
        settlement_rows.append({
            "entity_id": payment_id, "type": "payment", "debit": 0,
            "credit": round(ghost_amount - ghost_fee, 2), "amount": ghost_amount,
            "currency": "INR", "fee": round(ghost_amount * 0.02, 2),
            "tax": round(ghost_fee - ghost_amount * 0.02, 2), "settled": True,
            "settlement_id": settlement_id, "settlement_utr": f"1000000{payment_id[-3:]}",
            "order_id": order_id, "method": "upi",
            "description": "Order settled without a matching internal record",
            "created_at": "2026-08-22",
        })

    orders_df = pd.DataFrame(order_rows)
    settlement_df = pd.DataFrame(settlement_rows)

    # --- Ground truth: since every issue here is deliberately planted, we
    # know the TRUE status of every order — this lets the Accuracy page
    # validate the exact-verification engine's classifications against
    # known-correct answers, the same way evaluate.py does for the
    # tolerance engine, instead of only explaining why validation isn't
    # needed in the abstract.
    math_error_ids = set(math_error_targets.keys())
    unmatched_ids = {g[0] for g in ghost_orders}
    ground_truth_rows = []
    for row in settlement_rows:
        oid = row["order_id"]
        if oid in math_error_ids:
            true_status = "math_error"
        elif oid in unmatched_ids:
            true_status = "unmatched_to_reference"
        else:
            true_status = "verified"
        ground_truth_rows.append({"order_id": oid, "true_status": true_status})
    ground_truth_df = pd.DataFrame(ground_truth_rows)

    # --- Build the bank statement, with planted issue 3: batch mismatches ---
    # (2 mismatches, proportional to the larger batch count now that there
    # are ~10 settlement batches instead of 3)
    mismatch_batches = {"setl_000002": 500.00, "setl_000005": 250.00}
    batch_totals = settlement_df.groupby("settlement_id")["credit"].sum().reset_index()
    bank_rows = []
    for _, r in batch_totals.iterrows():
        deposit = r["credit"]
        if r["settlement_id"] in mismatch_batches:
            # The bank actually paid out LESS than the gateway report says it
            # should have — a real transfer shortfall.
            deposit = round(deposit - mismatch_batches[r["settlement_id"]], 2)
        bank_rows.append({
            "date": "2026-08-20",
            "narration": f"NEFT CR-RAZORPAY-{r['settlement_id']} payout",
            "credit": deposit,
        })
    # Realistic unrelated banking noise — always present on a real statement.
    bank_rows.append({"date": "2026-08-20", "narration": "OFFICE RENT AUGUST", "credit": 0})
    bank_rows.append({"date": "2026-08-21", "narration": "AWS CLOUD SERVICES", "credit": 0})
    bank_df = pd.DataFrame(bank_rows)

    orders_df.to_csv("data/sample_internal_orders.csv", index=False)
    settlement_df.to_csv("data/sample_razorpay_real_schema_settlement.csv", index=False)
    bank_df.to_csv("data/sample_razorpay_bank_statement.csv", index=False)
    ground_truth_df.to_csv("data/ground_truth_multi_source.csv", index=False)

    print(f"Generated {len(orders_df)} internal orders -> data/sample_internal_orders.csv")
    print(f"Generated {len(settlement_df)} settlement rows -> data/sample_razorpay_real_schema_settlement.csv")
    print(f"Generated {len(bank_df)} bank statement lines -> data/sample_razorpay_bank_statement.csv")
    print(f"Generated ground truth labels -> data/ground_truth_multi_source.csv (for accuracy validation)")
    print(f"Planted issues: {len(math_error_targets)} math errors {list(math_error_targets.keys())}, "
          f"{len(ghost_orders)} unmatched settlements {[g[0] for g in ghost_orders]}, "
          f"{len(mismatch_batches)} batch mismatches {mismatch_batches}.")


if __name__ == "__main__":
    generate()
