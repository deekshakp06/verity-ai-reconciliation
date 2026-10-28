"""Score Verity vs an Excel-style exact lookup against an answer key.

    python src/score_real_test.py payments.csv settlements.csv real_test_truth.csv

The answer key's `scenario` column decides what is correct:
  should auto-match : clean, late, ref_typo   (a valid settlement exists)
  should NOT match  : amount_mismatch, missing
  duplicate rows are excluded (the payment has a valid settlement, but the
  extra settlement line is a separate problem this score does not measure).
Excel-style baseline = exact reference lookup + amount within 1% of payment x 0.98,
the same rule used in the manual Excel test.
"""
import os
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from auto_reconciler import run_auto_reconciliation  # noqa: E402

SHOULD_MATCH = {"clean", "late", "ref_typo"}
SHOULD_FLAG = {"amount_mismatch", "missing"}


def main():
    p = pd.read_csv(sys.argv[1])
    s = pd.read_csv(sys.argv[2])
    truth = pd.read_csv(sys.argv[3]).set_index("txn_id")["scenario"].to_dict()

    out = run_auto_reconciliation(p, s)
    res = out.get("exact_result") or out["tolerance_result"]
    verity_matched = {d.payment_id for d in res["matched"]}

    first = {}
    for ref, amt in zip(s.iloc[:, 0], s.iloc[:, 1]):
        first.setdefault(ref, amt)
    excel_matched = set()
    for txn, amt, ref in zip(p["txn_id"], p["amount_inr"], p["internal_ref"]):
        got = first.get(ref)
        if got is not None and abs(got - amt * 0.98) <= amt * 0.01:
            excel_matched.add(txn)

    stats = {"Verity": defaultdict(lambda: [0, 0]), "Excel-style lookup": defaultdict(lambda: [0, 0])}
    for txn, scen in truth.items():
        if scen in SHOULD_MATCH:
            want = True
        elif scen in SHOULD_FLAG:
            want = False
        else:
            continue
        for name, matched in (("Verity", verity_matched), ("Excel-style lookup", excel_matched)):
            ok = (txn in matched) == want
            stats[name][scen][0] += ok
            stats[name][scen][1] += 1

    print(f"{'Scenario':<18}{'Verity':>14}{'Excel-style':>16}")
    scens = sorted({k for v in stats.values() for k in v})
    for sc in scens:
        v, e = stats["Verity"][sc], stats["Excel-style lookup"][sc]
        print(f"{sc:<18}{v[0]:>7}/{v[1]:<6}{e[0]:>9}/{e[1]:<6}")
    for name in stats:
        c = sum(x[0] for x in stats[name].values())
        t = sum(x[1] for x in stats[name].values())
        print(f"OVERALL {name}: {c}/{t} correct = {100 * c / t:.1f}%")


if __name__ == "__main__":
    main()