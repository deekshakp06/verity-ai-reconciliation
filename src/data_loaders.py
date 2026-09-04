"""
data_loaders.py
----------------
Verity's reconciliation logic (matcher.py, three_way_reconciler.py) only
ever operates on a pandas DataFrame — it never cares where that DataFrame
came from. This module proves that by providing loaders for the three most
common real-world sources: CSV, Excel, and SQL. Whichever one a real
Razorpay-style system actually uses, the same downstream logic applies
unchanged.

CSV and Excel are already wired into the web app (server.py) directly via
pandas' own readers — this module's CSV/Excel functions exist mainly to
document the pattern in one place. The SQL loader is the one that needs
explicit support code, since it requires a live connection rather than a
file upload.
"""

import pandas as pd
from logger import get_logger

logger = get_logger(__name__)


def load_csv(path_or_buffer) -> pd.DataFrame:
    """Any CSV — a file path, an uploaded file object, or a URL."""
    return pd.read_csv(path_or_buffer)


def load_excel_sheet(path_or_buffer, sheet_name=0) -> pd.DataFrame:
    """A single sheet from an Excel workbook. sheet_name can be an index,
    a name, or a loose keyword — see three_way_reconciler.load_three_way_excel
    for the keyword-matching version used for the 3-sheet workbook."""
    return pd.read_excel(path_or_buffer, sheet_name=sheet_name)


def load_sql_query(connection_string: str, query: str) -> pd.DataFrame:
    """
    Runs a SQL query against any database SQLAlchemy supports (Postgres,
    MySQL, SQLite, SQL Server, etc.) and returns the result as a DataFrame —
    from that point on, it's identical to a CSV or Excel load as far as the
    reconciliation engine is concerned.

    Example:
        df = load_sql_query(
            "postgresql://user:pass@host:5432/payments_db",
            "SELECT * FROM settlements WHERE settled_on >= '2026-08-01'",
        )

    Not wired into the web UI (that would need a connection-string input
    and credential handling, which is a real security surface worth doing
    properly rather than rushing) — but the reconciliation logic itself
    needs zero changes to accept this DataFrame once loaded.
    """
    from sqlalchemy import create_engine

    engine = create_engine(connection_string)
    try:
        df = pd.read_sql(query, engine)
        logger.info("Loaded %d rows via SQL query.", len(df))
        return df
    finally:
        engine.dispose()
