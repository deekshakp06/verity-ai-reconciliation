"""
exception_store.py
------------------
Persistent review state for exceptions: who looked at it, what they decided,
and why. Without this Verity is a report; with it, an exception is a work item
(open -> resolved / escalated) that survives page reloads and re-runs.

SQLAlchemy over SQLite by default (zero setup). Set VERITY_DB_URL to a
PostgreSQL URL to move to a shared database; no code changes are needed.
Decisions are keyed by (dataset_key, payment_id), where dataset_key is a hash
of the payments file, so re-running the SAME data keeps its review state and a
different dataset starts clean.

Limitation: SQLite on a host with an ephemeral disk (e.g. Render's free tier)
loses its data on redeploy. Use VERITY_DB_URL with a managed database there.
"""

import os
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import (Column, Integer, MetaData, String, Table, Text, create_engine, insert, select, update)

STATUSES = ("open", "resolved", "escalated")
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "verity.db")

_meta = MetaData()
_review = Table(
    "exception_review", _meta,
    Column("dataset_key", String(64), primary_key=True),
    Column("payment_id", String(128), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("note", Text, nullable=False, default=""),
    Column("reviewer", String(100), nullable=False, default=""),
    Column("updated_at", String(32), nullable=False),
)
_event = Table(
    "review_event", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset_key", String(64), nullable=False, index=True),
    Column("payment_id", String(128), nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16), nullable=False),
    Column("note", Text, nullable=False, default=""),
    Column("reviewer", String(100), nullable=False, default=""),
    Column("at", String(32), nullable=False),
)


def _normalize_url(url: str) -> str:
    """Hosts hand out 'postgres://' or 'postgresql://'; SQLAlchemy needs a driver name."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def dataset_key(df: pd.DataFrame) -> str:
    """Stable short id for a dataset: same rows -> same key."""
    import hashlib
    h = pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values.tobytes()
    return hashlib.sha1(h).hexdigest()[:12]


class ExceptionStore:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get("VERITY_DB_URL")
        if not url:
            path = os.path.abspath(_DEFAULT_PATH)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            url = f"sqlite:///{path}"
        self.engine = create_engine(_normalize_url(url), pool_pre_ping=True)
        _meta.create_all(self.engine)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def set_review(self, dataset_key: str, payment_id: str, status: str, note: str = "", reviewer: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if not dataset_key or not payment_id:
            raise ValueError("dataset_key and payment_id are required")
        note, reviewer, now = (note or "").strip()[:2000], (reviewer or "").strip()[:100], self._now()
        pid = str(payment_id)
        same = (_review.c.dataset_key == dataset_key) & (_review.c.payment_id == pid)
        with self.engine.begin() as c:
            prev = c.execute(select(_review.c.status).where(same)).first()
            fields = dict(status=status, note=note, reviewer=reviewer, updated_at=now)
            if prev:
                c.execute(update(_review).where(same).values(**fields))
            else:
                c.execute(insert(_review).values(dataset_key=dataset_key, payment_id=pid, **fields))
            c.execute(insert(_event).values(
                dataset_key=dataset_key, payment_id=pid, from_status=prev[0] if prev else "open",
                to_status=status, note=note, reviewer=reviewer, at=now))
        return {"payment_id": pid, "status": status, "note": note, "reviewer": reviewer, "updated_at": now}

    def get_reviews(self, dataset_key: str) -> dict:
        q = select(_review.c.payment_id, _review.c.status, _review.c.note, _review.c.reviewer,
                   _review.c.updated_at).where(_review.c.dataset_key == dataset_key)
        with self.engine.begin() as c:
            rows = c.execute(q).mappings().all()
        return {r["payment_id"]: dict(r) for r in rows}

    def history(self, dataset_key: str, payment_id: str) -> list:
        q = select(_event.c.from_status, _event.c.to_status, _event.c.note, _event.c.reviewer, _event.c.at).where(
            (_event.c.dataset_key == dataset_key) & (_event.c.payment_id == str(payment_id))).order_by(_event.c.id)
        with self.engine.begin() as c:
            rows = c.execute(q).mappings().all()
        return [dict(r) for r in rows]
