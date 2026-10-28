"""
excel_export.py
---------------
Builds the workbook a finance team actually works in: a Summary sheet, the
Exceptions (with review status, reviewer and notes) and the Matched pairs.
"""

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEAD = PatternFill("solid", start_color="1D4ED8")
_MONEY = '#,##0.00'


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _gap(r):
    if r.get("gap") is not None:
        return round(abs(_num(r["gap"]) or 0), 2)
    a, b = _num(r.get("amount_a")), _num(r.get("amount_b"))
    return round(abs(a - b), 2) if a is not None and b is not None else None


def _sheet(wb, title, headers, rows, money_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), _HEAD
    for r in rows:
        ws.append(r)
    for col in money_cols:
        for cell in ws[get_column_letter(col)][1:]:
            cell.number_format = _MONEY
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[i - 1] if r[i - 1] is not None else "")) for r in rows[:200]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str) and len(cell.value) > 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def build_workbook(matched: list, exceptions: list, reviews: dict, kind_of=None, meta: dict | None = None) -> bytes:
    """matched / exceptions: lists of decision dicts as returned by the API.
    reviews: {payment_id: {status, note, reviewer, updated_at}}.
    kind_of: optional function(exception_dict) -> label for the Type column."""
    meta = meta or {}
    reviews = reviews or {}
    kind_of = kind_of or (lambda e: "")
    status = lambda e: (reviews.get(str(e.get("payment_id"))) or {}).get("status", "open")

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    total = len(matched) + len(exceptions)
    open_n = sum(1 for e in exceptions if status(e) == "open")
    atrisk = sum(_num(e.get("amount_a")) or 0 for e in exceptions)
    lines = [
        ("Verity reconciliation report", None),
        ("Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")),
        ("Method", meta.get("method", "")),
        ("Records", total),
        ("Auto-matched", len(matched)),
        ("Match rate", f"{(100 * len(matched) / total):.1f}%" if total else "n/a"),
        ("Exceptions", len(exceptions)),
        ("  open", open_n),
        ("  escalated", sum(1 for e in exceptions if status(e) == "escalated")),
        ("  resolved", sum(1 for e in exceptions if status(e) == "resolved")),
        ("Gross amount in exceptions", round(atrisk, 2)),
    ]
    for k, v in lines:
        ws.append([k, v])
    ws["A1"].font = Font(bold=True, size=14)
    for r in range(2, len(lines) + 1):
        ws.cell(r, 1).font = Font(bold=True)
    ws.cell(len(lines), 2).number_format = _MONEY
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 30, 40

    exc_rows = []
    for e in sorted(exceptions, key=lambda x: -(_gap(x) or 0)):
        rv = reviews.get(str(e.get("payment_id"))) or {}
        exc_rows.append([e.get("payment_id"), kind_of(e), rv.get("status", "open"), _num(e.get("amount_a")),
                         _num(e.get("amount_b")), _gap(e), e.get("settlement_ref"), e.get("reason"),
                         e.get("explanation"), rv.get("reviewer", ""), rv.get("note", ""), rv.get("updated_at", "")])
    _sheet(wb, "Exceptions", ["Payment", "Type", "Review status", "Amount A", "Amount B", "Gap", "Closest settlement",
                              "Why flagged", "Explanation", "Reviewer", "Note", "Reviewed at (UTC)"], exc_rows, money_cols=(4, 5, 6))

    ok_rows = [[m.get("payment_id"), m.get("settlement_ref"), _num(m.get("amount_a")), _num(m.get("amount_b")),
                m.get("date_a"), m.get("date_b"), "exact" if m.get("exact") else _num(m.get("confidence")),
                ", ".join(m.get("flags") or [])] for m in matched]
    _sheet(wb, "Matched", ["Payment", "Settlement", "Amount A", "Amount B", "Payment date", "Settlement date",
                           "Confidence", "Flags"], ok_rows, money_cols=(3, 4))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()