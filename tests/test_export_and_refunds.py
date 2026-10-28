import io, os, sys
import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from matcher import reconcile
from excel_export import build_workbook


def _frames():
    pay = pd.DataFrame({"id": ["T1", "T2", "T3_REFUND", "T4"], "amount": [1000.0, 2000.0, -500.0, 750.0],
                        "date": ["2026-08-01"] * 4, "internal_ref": ["REF-A", "REF-B", "REF-C", "REF-D"]})
    setl = pd.DataFrame({"id": ["REF-A", "REF-B", "REF-D", "REF-X"], "amount": [976.4, 1952.8, 732.3, 500.0],
                         "date": ["2026-08-02"] * 4})
    return pay, setl


def test_refund_is_never_matched_and_is_labelled():
    r = reconcile(*_frames())
    refund = next(d for d in r["decisions"] if d.payment_id == "T3_REFUND")
    assert refund.status == "exception" and "refund" in refund.flags
    assert refund.settlement_ref is None          # it did not steal REF-X (500) or any settlement
    assert sum(1 for d in r["decisions"] if d.status == "matched") == 3


def test_negative_amount_without_refund_in_id_is_also_routed():
    pay, setl = _frames()
    pay.loc[3, "amount"] = -750.0
    d = next(x for x in reconcile(pay, setl)["decisions"] if x.payment_id == "T4")
    assert d.status == "exception" and "refund" in d.flags


def test_excel_workbook_contents():
    matched = [{"payment_id": "T1", "settlement_ref": "REF-A", "amount_a": 1000, "amount_b": 976.4,
                "date_a": "2026-08-01", "date_b": "2026-08-02", "confidence": 96, "flags": ["late_settlement"]}]
    exceptions = [{"payment_id": "T9", "amount_a": 500, "amount_b": 450, "reason": "r", "explanation": "e"},
                  {"payment_id": "T8", "amount_a": 900, "amount_b": 600, "reason": "r2", "explanation": "e2"}]
    reviews = {"T9": {"status": "resolved", "note": "checked bank", "reviewer": "D", "updated_at": "2026-10-28T00:00:00+00:00"}}
    wb = load_workbook(io.BytesIO(build_workbook(matched, exceptions, reviews, kind_of=lambda e: "Amount mismatch")))
    assert wb.sheetnames == ["Summary", "Exceptions", "Matched"]
    rows = list(wb["Exceptions"].iter_rows(values_only=True))
    assert rows[1][0] == "T8" and rows[1][5] == 300          # largest gap first
    assert rows[2][0] == "T9" and rows[2][2] == "resolved" and rows[2][10] == "checked bank"
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True)}
    assert summary["Exceptions"] == 2 and summary["  resolved"] == 1 and summary["  open"] == 1


def test_export_endpoint(tmp_path, monkeypatch):
    import server
    from exception_store import ExceptionStore
    monkeypatch.setattr(server, "_store", ExceptionStore(f"sqlite:///{tmp_path / 'x.db'}"))
    r = server.app.test_client().post("/api/export", json={
        "matched": [], "exceptions": [{"payment_id": "T1", "amount_a": 10, "amount_b": 5}],
        "kinds": {"T1": "Amount mismatch"}, "dataset_key": "d", "method": "Tolerance matching"})
    assert r.status_code == 200 and r.data[:2] == b"PK"        # an .xlsx is a zip file