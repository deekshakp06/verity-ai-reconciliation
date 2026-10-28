import os, sys
from decimal import Decimal
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from money import D, money_sum, paise
from auto_reconciler import verify_exact


def test_decimal_avoids_float_drift():
    assert 0.1 + 0.2 != 0.3                               # the problem
    assert D(0.1) + D(0.2) == D(0.3)                      # the fix
    assert money_sum([0.1] * 10000) == Decimal("1000.00")  # float sum drifts; this does not
    acc = 0.0
    for _ in range(10000):
        acc += 0.1                                         # how a running float total behaves
    assert acc != 1000.0


def test_parsing_and_rounding():
    assert D("1,234.50") == Decimal("1234.50")
    assert D(2.675) == Decimal("2.68")                    # half-up on the printed value, not binary 2.67499...
    assert paise(40843.09) == 4084309


@pytest.mark.parametrize("bad", [None, float("nan"), "abc", ""])
def test_invalid_amounts_raise(bad):
    with pytest.raises(ValueError):
        D(bad)


def _exact(net, gross=100.10, fee=2.00, tax=0.36):
    ref = pd.DataFrame({"order_id": ["o1"], "amount": [gross], "order_date": ["2026-08-01"]})
    fees = pd.DataFrame({"order_id": ["o1"], "gross": [gross], "fee": [fee], "tax": [tax], "net": [net], "settled_on": ["2026-08-02"]})
    cols = {"gross": "gross", "fee": "fee", "tax": "tax", "net": "net"}
    return verify_exact(ref, fees, cols, "order_id")["orders"][0]


def test_exact_engine_boundaries_are_exact():
    # 100.10 - 2.00 - 0.36 = 97.74 exactly; 2-paise rounding tolerance either side is allowed
    assert _exact(97.74)["status"] == "verified"
    assert _exact(97.76)["status"] == "verified"           # +2 paise: inside tolerance
    assert _exact(97.77)["status"] == "math_error"         # +3 paise: outside
    assert _exact(97.71)["status"] == "math_error"         # -3 paise: outside
    r = _exact(97.00)
    assert r["status"] == "math_error" and r["diff"] == -0.74   # exact diff, no 0.7399999...
