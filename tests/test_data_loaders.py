"""
test_data_loaders.py
----------------------
Proves the claim "Verity can reconcile data from CSV, Excel, or SQL" with
actual working code, not just documentation — each loader is exercised for
real and its output is fed into the real reconcile() engine.
"""

import os
import sys
import sqlite3
import tempfile
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_loaders import load_csv, load_excel_sheet, load_sql_query
from matcher import reconcile


def test_csv_source_feeds_the_real_engine(tmp_path):
    csv_path = tmp_path / "payments.csv"
    pd.DataFrame([{"id": "TXN1", "amount": 500.0, "date": "2026-08-01"}]).to_csv(csv_path, index=False)

    df = load_csv(csv_path)
    settlements = pd.DataFrame([{"id": "TXN1", "amount": 500.0, "date": "2026-08-02"}])
    result = reconcile(df, settlements)
    assert result["match_rate"] == 100.0


def test_excel_source_feeds_the_real_engine(tmp_path):
    xlsx_path = tmp_path / "payments.xlsx"
    pd.DataFrame([{"id": "TXN1", "amount": 750.0, "date": "2026-08-01"}]).to_excel(xlsx_path, index=False)

    df = load_excel_sheet(xlsx_path)
    settlements = pd.DataFrame([{"id": "TXN1", "amount": 750.0, "date": "2026-08-02"}])
    result = reconcile(df, settlements)
    assert result["match_rate"] == 100.0


def test_sql_source_feeds_the_real_engine(tmp_path):
    db_path = tmp_path / "demo.db"
    conn = sqlite3.connect(db_path)
    pd.DataFrame([{"id": "TXN1", "amount": 1000.0, "date": "2026-08-01"}]).to_sql(
        "payments", conn, if_exists="replace", index=False
    )
    conn.close()

    df = load_sql_query(f"sqlite:///{db_path}", "SELECT * FROM payments")
    settlements = pd.DataFrame([{"id": "TXN1", "amount": 1000.0, "date": "2026-08-02"}])
    result = reconcile(df, settlements)
    assert result["match_rate"] == 100.0
