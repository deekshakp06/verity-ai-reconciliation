"""
generate_data.py
-----------------
Simulates two data sources that a real reconciliation agent would face:

1. payment_log.csv   -> Razorpay's internal record of payments
2. settlement_file.csv -> The bank's settlement record for the same payments

IMPORTANT: the two files use DIFFERENT column names on purpose
(e.g. "txn_id" vs "reference_no", "amount_inr" vs "settled_amount").
This is what makes the schema-agnostic mapper (src/schema_mapper.py) necessary
instead of just hardcoding column names.

REALISTIC FEE MODELING: every settled transaction deducts a standard payment
gateway fee — 2% MDR + 18% GST on that fee (~2.36% effective total), based on
Razorpay's own published pricing. A genuinely fee-FREE settlement is not the
normal case in real life; a "clean" match here means the fee matches the
expected standard rate, not that no fee was taken at all. AMOUNT_TOLERANCE_PCT
(see config.py) is set wide enough to recognize this standard fee as normal.

We deliberately inject several kinds of real-world mismatches so the matching
engine has genuine problems to solve:
  - Clean matches            (standard ~2.36% fee — should auto-match)
  - Fee anomalies            (fee well beyond the standard rate -> exception)
  - Delayed settlement dates (normal lag -> should still auto-match)
  - Missing settlement entry (payment exists, settlement never arrived -> exception)
  - Duplicate entries         (same txn appears twice -> exception)
"""

import pandas as pd
import numpy as np
import random
import string
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

NUM_RECORDS = 150

# Razorpay's own published standard rate: 2% MDR + 18% GST on that fee.
# 2% + (2% * 0.18) = 2.36% effective deduction on a normal transaction.
STANDARD_MDR_PCT = 2.0
GST_ON_FEE_PCT = 18.0


def standard_fee(amount: float) -> float:
    # Rounds MDR and GST SEPARATELY (as a real invoicing system actually
    # does, step by step) rather than rounding one combined figure. This
    # naturally produces tiny, genuine paisa-level variation between
    # transactions instead of every fee being a perfectly identical
    # percentage of its own amount.
    mdr = round(amount * (STANDARD_MDR_PCT / 100), 2)
    gst = round(mdr * (GST_ON_FEE_PCT / 100), 2)
    return round(mdr + gst, 2)


def realistic_settlement_lag_days() -> int:
    # Real settlement lag varies transaction to transaction — commonly
    # T+1 to T+3 business days — rather than being a fixed constant.
    return random.randint(1, 3)


def random_txn_id(i):
    return f"TXN{i:05d}"


def random_ref_id(txn_id):
    # bank reference numbers look nothing like our internal IDs on purpose
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"REF-{suffix}"


def generate():
    base_date = datetime(2026, 8, 1)
    payment_rows = []
    settlement_rows = []
    ground_truth_rows = []  # kept separate — the matcher never sees this

    for i in range(1, NUM_RECORDS + 1):
        txn_id = random_txn_id(i)
        ref_id = random_ref_id(txn_id)
        amount = round(random.uniform(200, 50000), 2)
        pay_date = base_date + timedelta(days=random.randint(0, 20))

        scenario = np.random.choice(
            ["clean", "fee_mismatch", "delayed", "missing", "duplicate"],
            p=[0.65, 0.15, 0.10, 0.06, 0.04],
        )
        # Ground truth: "clean" and "delayed" are genuinely the same transaction
        # and SHOULD end up auto-matched. Everything else legitimately needs a
        # human look, so the correct agent behavior is to flag it as an exception.
        should_match = scenario in ("clean", "delayed")
        ground_truth_rows.append({"txn_id": txn_id, "scenario": scenario, "should_match": should_match})

        payment_rows.append(
            {
                "txn_id": txn_id,
                "amount_inr": amount,
                "payment_date": pay_date.strftime("%Y-%m-%d"),
                "merchant": f"Merchant_{random.randint(1, 30)}",
                "internal_ref": ref_id,
            }
        )

        if scenario == "clean":
            # Standard, expected fee — this IS the normal case, not an anomaly.
            fee = standard_fee(amount)
            settlement_rows.append(
                {
                    "reference_no": ref_id,
                    "settled_amount": round(amount - fee, 2),
                    "settlement_date": (pay_date + timedelta(days=realistic_settlement_lag_days())).strftime("%Y-%m-%d"),
                    "bank_code": f"BANK_{random.randint(1, 5)}",
                }
            )
        elif scenario == "fee_mismatch":
            # A genuine ANOMALY: an additional, unexplained proportional
            # deduction well beyond the standard rate — e.g. an incorrect
            # fee tier, a disputed charge, or a data error. Proportional
            # (not a flat rupee amount) so the anomaly stays clearly
            # separated from the standard rate across the whole amount
            # range, not just for small transactions.
            standard = standard_fee(amount)
            anomaly_pct = random.uniform(3.0, 10.0)  # extra % on top of standard
            total_fee = standard + round(amount * (anomaly_pct / 100), 2)
            settlement_rows.append(
                {
                    "reference_no": ref_id,
                    "settled_amount": round(amount - total_fee, 2),
                    "settlement_date": (pay_date + timedelta(days=realistic_settlement_lag_days())).strftime("%Y-%m-%d"),
                    "bank_code": f"BANK_{random.randint(1, 5)}",
                }
            )
        elif scenario == "delayed":
            # Still the standard fee — only the timing is unusual, not the amount.
            fee = standard_fee(amount)
            settlement_rows.append(
                {
                    "reference_no": ref_id,
                    "settled_amount": round(amount - fee, 2),
                    "settlement_date": (pay_date + timedelta(days=random.randint(6, 10))).strftime("%Y-%m-%d"),
                    "bank_code": f"BANK_{random.randint(1, 5)}",
                }
            )
        elif scenario == "missing":
            pass  # no settlement row at all -> genuine exception
        elif scenario == "duplicate":
            fee = standard_fee(amount)
            # Both duplicate rows share the SAME lag on purpose — they're
            # meant to be near-identical copies of each other (that's what
            # makes them a genuine duplicate-settlement signal for the
            # matcher to catch), not two independently-timed settlements.
            lag = realistic_settlement_lag_days()
            for _ in range(2):
                settlement_rows.append(
                    {
                        "reference_no": ref_id,
                        "settled_amount": round(amount - fee, 2),
                        "settlement_date": (pay_date + timedelta(days=lag)).strftime("%Y-%m-%d"),
                        "bank_code": f"BANK_{random.randint(1, 5)}",
                    }
                )

    payment_df = pd.DataFrame(payment_rows)
    settlement_df = pd.DataFrame(settlement_rows)
    ground_truth_df = pd.DataFrame(ground_truth_rows)

    payment_df.to_csv("data/payment_log.csv", index=False)
    settlement_df.to_csv("data/settlement_file.csv", index=False)
    ground_truth_df.to_csv("data/ground_truth.csv", index=False)

    print(f"Generated {len(payment_df)} payment records -> data/payment_log.csv")
    print(f"Generated {len(settlement_df)} settlement records -> data/settlement_file.csv")
    print(f"Generated ground truth labels -> data/ground_truth.csv (for accuracy validation only)")
    print("Column names deliberately differ between files to test schema mapping.")


if __name__ == "__main__":
    generate()
