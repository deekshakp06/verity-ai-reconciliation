"""
benchmark.py
------------
Reproducible speed numbers:   python src/benchmark.py 1000 10000

Generates n synthetic payments with generate_data.py in a temp folder (so your
real data/ is untouched) and times the matcher on them.
"""
import os
import random
import sys
import tempfile
import time

import numpy as np
import pandas as pd

import generate_data
from matcher import reconcile
from schema_mapper import apply_mapping, map_schema


def run(n: int) -> float:
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data"))
        os.chdir(tmp)
        try:
            random.seed(1)
            np.random.seed(1)
            generate_data.NUM_RECORDS = n
            generate_data.generate()
            p, s = pd.read_csv("data/payment_log.csv"), pd.read_csv("data/settlement_file.csv")
        finally:
            os.chdir(cwd)
    pm = apply_mapping(p, map_schema(p, "payment_log"))
    sm = apply_mapping(s, map_schema(s, "settlement_file"))
    t = time.perf_counter()
    reconcile(pm, sm)
    return time.perf_counter() - t


if __name__ == "__main__":
    import logging
    logging.disable(logging.INFO)
    for n in [int(a) for a in sys.argv[1:]] or [1000, 10000]:
        sec = run(n)
        print(f"{n:>7} payments -> {sec:6.2f}s  ({n / sec:,.0f} records/sec)")
