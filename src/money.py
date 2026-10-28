"""
money.py
--------
Exact money arithmetic. Floats cannot represent most decimal amounts
(0.1 + 0.2 != 0.3), and a reconciliation tool that compares "gross - fee - tax"
to a reported net must not be wrong by a rounding artifact. Amounts are read
into Decimal (via their text form, never from a float's binary value), rounded
to paise, and summed exactly.
"""

from decimal import Decimal, ROUND_HALF_UP

_PAISA = Decimal("0.01")


def D(value) -> Decimal:
    """Amount -> Decimal rounded to 2 places. Raises ValueError for missing or non-numeric values."""
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except Exception:
        raise ValueError(f"Not a valid amount: {value!r}")
    if not d.is_finite():
        raise ValueError(f"Not a valid amount: {value!r}")
    return d.quantize(_PAISA, rounding=ROUND_HALF_UP)


def money_sum(values) -> Decimal:
    """Exact sum of amounts (no float drift across thousands of rows)."""
    return sum((D(v) for v in values), Decimal("0.00"))


def paise(value) -> int:
    """Amount as integer paise; safe as a dict key or for equality checks."""
    return int(D(value) * 100)
