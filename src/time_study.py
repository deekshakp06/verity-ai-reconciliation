"""
time_study.py
-------------
Times Verity on ANY two CSV files and prints a short report you can quote.

    python src/time_study.py payments.csv settlements.csv
    python src/time_study.py payments.csv settlements.csv --manual-minutes 95

--manual-minutes is how long it took a person to do the same matching by hand
(use a stopwatch on a real person; do not estimate). Without it the report
only states Verity's measured time.
"""

import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GROQ_API_KEY", "")      # time the matching itself, not network explanations
os.environ.setdefault("OPENAI_API_KEY", "")

from auto_reconciler import run_auto_reconciliation  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payments"), ap.add_argument("settlements")
    ap.add_argument("--manual-minutes", type=float, default=None)
    a = ap.parse_args()

    p, s = pd.read_csv(a.payments), pd.read_csv(a.settlements)
    t0 = time.perf_counter()
    out = run_auto_reconciliation(p, s)
    secs = time.perf_counter() - t0
    d = out["decision"]
    res = out.get("exact_result") or out["tolerance_result"]
    if d["strategy"] == "exact":
        n_ok = sum(1 for o in res["orders"] if o["status"] == "verified")
        total = res["total"]
    else:
        n_ok, total = len(res["matched"]), len(res["matched"]) + len(res["exceptions"])
    review = total - n_ok

    print(f"Files:            {os.path.basename(a.payments)} ({len(p)} rows), {os.path.basename(a.settlements)} ({len(s)} rows)")
    print(f"Method chosen:    {d['strategy']}")
    print(f"Auto-matched:     {n_ok} of {total} ({100 * n_ok / total:.1f}%)")
    print(f"Sent to review:   {review}")
    print(f"Verity time:      {secs:.2f} s")
    if a.manual_minutes:
        manual = a.manual_minutes * 60
        print(f"Manual time:      {a.manual_minutes:.0f} min (measured by you)")
        print(f"Speed-up:         about {manual / secs:,.0f}x on the matching step "
              f"(a person would still review the {review} exceptions)")


if __name__ == "__main__":
    main()