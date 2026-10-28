"""
Tests for the identity-first matching rewrite and the hard (adversarial) test set.
Each of these corresponds to a real failure found by measuring accuracy on
data/ground_truth_hard.csv, not by guessing.
"""
import os
import sys
import random

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import evaluate
import generate_hard_data
from matcher import reconcile, _strip_common_prefix, _scores, _score_values


def _frames(pay, setl):
    p = pd.DataFrame([{"id": f"TXN{i}", "amount": a, "date": d, "internal_ref": r} for i, (a, d, r) in enumerate(pay)])
    s = pd.DataFrame([{"id": r, "amount": a, "date": d} for a, d, r in setl])
    return p, s


def test_decoy_with_similar_amount_but_different_reference_is_not_matched():
    # Payment has NO settlement. Someone else's settlement has a near-identical net
    # amount and date. Old behaviour: matched it (shared "REF-" prefix inflated similarity).
    p, s = _frames([(1000.0, "2026-08-01", "REF-AAAA1111")], [(976.4, "2026-08-03", "REF-ZZZZ9999")])
    d = reconcile(p, s)["decisions"][0]
    assert d.status == "exception"
    assert "reference code" in d.reason


def test_decoy_cannot_steal_the_settlement_that_belongs_to_a_later_payment():
    p, s = _frames(
        [(1000.0, "2026-08-01", "REF-AAAA1111"), (1000.0, "2026-08-01", "REF-BBBB2222")],
        [(976.4, "2026-08-03", "REF-BBBB2222")],
    )
    d = {x.payment_id: x for x in reconcile(p, s)["decisions"]}
    assert d["TXN0"].status == "exception"
    assert d["TXN1"].status == "matched" and d["TXN1"].settlement_ref == "REF-BBBB2222"


def test_same_amount_and_date_payments_each_match_their_own_reference():
    p, s = _frames(
        [(5000.0, "2026-08-01", "REF-AAAA1111"), (5000.0, "2026-08-01", "REF-BBBB2222")],
        [(4882.0, "2026-08-02", "REF-BBBB2222"), (4882.0, "2026-08-02", "REF-AAAA1111")],
    )
    r = reconcile(p, s)["decisions"]
    assert [x.settlement_ref for x in r] == ["REF-AAAA1111", "REF-BBBB2222"]
    assert all(x.status == "matched" for x in r)


def test_reference_with_one_typo_still_matches():
    p, s = _frames([(2000.0, "2026-08-01", "REF-AB12CD34")], [(1952.8, "2026-08-03", "REF-AB12CD3X")])
    assert reconcile(p, s)["decisions"][0].status == "matched"


def test_reference_in_a_different_format_still_matches():
    p, s = _frames([(2000.0, "2026-08-01", "REF-AB12CD34")], [(1952.8, "2026-08-03", "ref_ab12cd34")])
    assert reconcile(p, s)["decisions"][0].status == "matched"


def test_late_settlement_with_verified_reference_is_matched_and_flagged():
    p, s = _frames([(2000.0, "2026-08-01", "REF-AB12CD34")], [(1952.8, "2026-08-13", "REF-AB12CD34")])
    r = reconcile(p, s)
    d = r["decisions"][0]
    assert d.status == "matched" and d.flags == ["late_settlement"]
    assert r["late_flagged"] == 1


def test_very_late_settlement_stays_an_exception():
    p, s = _frames([(2000.0, "2026-08-01", "REF-AB12CD34")], [(1952.8, "2026-09-25", "REF-AB12CD34")])
    d = reconcile(p, s)["decisions"][0]
    assert d.status == "exception" and d.flags == []


def test_common_column_prefix_is_stripped_before_comparing():
    assert _strip_common_prefix(["REFAB12", "REFCD34"]) == ["AB12", "CD34"]
    assert _strip_common_prefix(["AB12", "CD34"]) == ["AB12", "CD34"]


def test_vectorized_scores_equal_the_readable_scoring_function():
    rng = random.Random(3)
    for _ in range(300):
        pa, sa = rng.uniform(100, 50000), rng.uniform(100, 50000) if rng.random() < .3 else None
        sa = sa if sa is not None else pa * rng.uniform(0.95, 1.0)
        gap = rng.randint(0, 30)
        sim = rng.choice([100.0, 83.3, 40.0, 0.0])
        for cross in (True, False):
            fast = _scores(pa, np.datetime64("2026-08-01", "ns"), np.array([sa]),
                           np.array([np.datetime64("2026-08-01", "ns") + np.timedelta64(gap, "D")]),
                           np.array([sim]), cross)[0]
            # np.round and Python's round() can disagree by 0.1 on exact .x5 ties. The
            # vectorized score only PICKS the best candidate; the confidence reported
            # to users always comes from _score_values, so a tie-level difference is harmless.
            assert abs(fast - _score_values(pa, sa, gap, sim, cross)[0]) <= 0.1 + 1e-9


def _hard_metrics(tmp_path):
    generate_hard_data.generate(out_dir=str(tmp_path))
    p = pd.read_csv(tmp_path / "hard_payment_log.csv")
    s = pd.read_csv(tmp_path / "hard_settlement_file.csv")
    from schema_mapper import map_schema, apply_mapping
    pm, sm = apply_mapping(p, map_schema(p, "payment_log")), apply_mapping(s, map_schema(s, "settlement_file"))
    result = reconcile(pm, sm)
    return result, evaluate.evaluate_against_ground_truth(result["decisions"], str(tmp_path / "ground_truth_hard.csv"))


def test_hard_set_with_fee_check_catches_small_fee_anomalies(tmp_path):
    result, m = _hard_metrics(tmp_path)
    assert result["fee_check"]["applied"] and result["fee_check"]["flagged"] > 0
    assert abs(result["fee_check"]["typical_pct"] - 2.36) < 0.01   # learned the fee rate from the data
    assert m["recall"] == 100.0 and m["precision"] == 100.0
    assert m["per_scenario"]["small_fee_anomaly"]["pct"] == 100.0


def test_without_fee_check_small_fee_anomaly_is_the_known_weakness(tmp_path, monkeypatch):
    import matcher
    monkeypatch.setattr(matcher, "FEE_DEVIATION_PP", 100.0)   # effectively disables the check
    _, m = _hard_metrics(tmp_path)
    assert m["per_scenario"]["small_fee_anomaly"]["pct"] < 100.0
    for scenario, v in m["per_scenario"].items():
        if scenario != "small_fee_anomaly":
            assert v["pct"] == 100.0, scenario


def test_fee_check_skipped_when_fee_rate_is_not_consistent():
    # amounts shrink by a different percentage every time -> no fee structure to learn
    n = 40
    pay = pd.DataFrame({"id": [f"T{i}" for i in range(n)], "amount": [1000.0 + i for i in range(n)],
                        "date": ["2026-08-01"] * n, "internal_ref": [f"REF-{i:05d}" for i in range(n)]})
    setl = pd.DataFrame({"id": [f"REF-{i:05d}" for i in range(n)],
                         "amount": [(1000.0 + i) * (1 - 0.001 * (i % 20)) for i in range(n)],
                         "date": ["2026-08-02"] * n})
    assert reconcile(pay, setl)["fee_check"]["applied"] is False


def test_two_thousand_records_reconcile_in_seconds(tmp_path):
    import time
    generate_hard_data.generate(out_dir=str(tmp_path), n=2000)
    p = pd.read_csv(tmp_path / "hard_payment_log.csv")
    s = pd.read_csv(tmp_path / "hard_settlement_file.csv")
    from schema_mapper import map_schema, apply_mapping
    pm, sm = apply_mapping(p, map_schema(p, "payment_log")), apply_mapping(s, map_schema(s, "settlement_file"))
    t = time.perf_counter()
    reconcile(pm, sm)
    assert time.perf_counter() - t < 15
