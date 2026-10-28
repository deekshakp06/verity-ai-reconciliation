"""
generate_hard_data.py
---------------------
An INDEPENDENT, deliberately adversarial test set. generate_data.py builds the
demo data with the matcher's own assumptions in mind, so a perfect score there
proves little. This set is written to be uncomfortable: messy reference codes,
decoy settlements, same-amount collisions and tiny fee anomalies.

Every row carries a ground-truth label (should_match), and the scenarios below
include cases Verity is EXPECTED to get wrong. Results are reported honestly.

    python src/generate_hard_data.py     ->  data/hard_*.csv
"""
import os
import random
import string
from datetime import datetime, timedelta

import pandas as pd

N = 600
SEED = 7  # fixed, so the published numbers are reproducible


def _ref(rng):
    return "REF-" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=8))


def _typo(ref, rng):
    i = rng.randrange(4, len(ref))
    return ref[:i] + rng.choice([c for c in string.ascii_uppercase + string.digits if c != ref[i]]) + ref[i + 1:]


def _fee(amount):
    return round(amount * 0.0236, 2)  # 2% MDR + 18% GST on the fee


def generate(out_dir="data", n=N, seed=SEED):
    rng = random.Random(seed)
    base = datetime(2026, 8, 1)
    pay, setl, truth = [], [], []

    def add_payment(i, amount, day, ref):
        pay.append({"txn_id": f"TXN{i:05d}", "amount_inr": amount,
                    "payment_date": (base + timedelta(days=day)).strftime("%Y-%m-%d"),
                    "merchant": f"Merchant_{rng.randint(1, 30)}", "internal_ref": ref})

    def add_settlement(ref, amount, day, lag):
        setl.append({"reference_no": ref, "settled_amount": round(amount, 2),
                     "settlement_date": (base + timedelta(days=day + lag)).strftime("%Y-%m-%d"),
                     "bank_code": f"BANK_{rng.randint(1, 5)}"})

    scenarios = ["clean", "ref_typo", "ref_format", "collision", "late_8_25d", "very_late_40d",
                 "missing_with_decoy", "small_fee_anomaly", "fee_anomaly", "duplicate"]
    weights = [0.30, 0.08, 0.08, 0.10, 0.08, 0.04, 0.10, 0.08, 0.08, 0.06]
    prev_amount = prev_day = None
    for i in range(1, n + 1):
        sc = rng.choices(scenarios, weights)[0]
        amount, day, ref = round(rng.uniform(200, 50000), 2), rng.randint(0, 20), _ref(rng)
        if sc == "collision" and prev_amount is not None:
            amount, day = prev_amount, prev_day          # same amount AND date as the previous payment
        prev_amount, prev_day = amount, day
        add_payment(i, amount, day, ref)
        lag, net = rng.randint(1, 3), amount - _fee(amount)
        should = True
        if sc in ("clean", "collision"):
            add_settlement(ref, net, day, lag)
        elif sc == "ref_typo":
            add_settlement(_typo(ref, rng), net, day, lag)                     # one character wrong
        elif sc == "ref_format":
            add_settlement(ref.lower().replace("-", "_"), net, day, lag)       # ref_ab12cd34
        elif sc == "late_8_25d":
            add_settlement(ref, net, day, rng.randint(8, 25))                  # late, same transaction
        elif sc == "very_late_40d":
            add_settlement(ref, net, day, rng.randint(40, 60)); should = False
        elif sc == "missing_with_decoy":
            # no settlement for this payment, but someone else's looks very similar
            add_settlement(_ref(rng), net * rng.uniform(0.998, 1.002), day + rng.randint(0, 2), lag); should = False
        elif sc == "small_fee_anomaly":
            add_settlement(ref, net - amount * rng.uniform(0.001, 0.004), day, lag); should = False   # 0.1-0.4% extra
        elif sc == "fee_anomaly":
            add_settlement(ref, net - amount * rng.uniform(0.03, 0.10), day, lag); should = False
        elif sc == "duplicate":
            for _ in range(2):
                add_settlement(ref, net, day, lag)
            should = False
        truth.append({"txn_id": f"TXN{i:05d}", "scenario": sc, "should_match": should})

    rng.shuffle(setl)  # settlement files never arrive in payment order
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(pay).to_csv(f"{out_dir}/hard_payment_log.csv", index=False)
    pd.DataFrame(setl).to_csv(f"{out_dir}/hard_settlement_file.csv", index=False)
    pd.DataFrame(truth).to_csv(f"{out_dir}/ground_truth_hard.csv", index=False)
    return len(pay), len(setl)


if __name__ == "__main__":
    p, s = generate()
    print(f"Generated {p} payments / {s} settlements -> data/hard_*.csv, data/ground_truth_hard.csv")
