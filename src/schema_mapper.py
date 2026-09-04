"""
schema_mapper.py
-----------------
THE DIFFERENTIATOR.

Every reconciliation tool needs to know which column means "amount", which means
"date", which means "transaction ID". Normally a human configures this once per
data source. This module makes the agent figure it out on its own by looking at
column names + a few sample values, so it works on a data source it has never
seen before.

Two modes:
  - LLM mode: if an API key is available, ask the model to propose the mapping.
  - Heuristic fallback: keyword + data-type based matching, so the whole system
    still works with zero API cost / zero internet access for a demo.

Canonical fields we need to find in ANY source file:
  - id        (a unique reference/transaction identifier)
  - amount    (a monetary value)
  - date      (a date the transaction/settlement happened)
"""

import os
import json
import re
import pandas as pd

from config import CANONICAL_FIELDS, OPENAI_MODEL, LLM_TEMPERATURE_MAPPING
from logger import get_logger

logger = get_logger(__name__)

# Keyword hints used by the heuristic fallback (and as a sanity-check on LLM output)
KEYWORD_HINTS = {
    "id": ["id", "txn", "transaction", "reference", "ref", "no", "code"],
    "amount": ["amount", "amt", "value", "inr", "settled", "price", "total", "paid", "billed", "cents", "gross"],
    "date": ["date", "time", "settled_on", "timestamp", "created", "cleared", "paid_on", "_on"],
}


def _heuristic_guess(columns):
    """Score each column against keyword hints and pick the best match per field."""
    mapping = {}
    used = set()
    for field, keywords in KEYWORD_HINTS.items():
        best_col, best_score = None, 0
        for col in columns:
            if col in used:
                continue
            col_lower = col.lower()
            score = sum(1 for kw in keywords if kw in col_lower)
            if score > best_score:
                best_col, best_score = col, score
        if best_col:
            mapping[field] = best_col
            used.add(best_col)
    return mapping


def _llm_guess(columns, sample_rows):
    """
    Ask an LLM to map this unfamiliar file's columns to our canonical fields.
    Falls back silently to heuristic mode if no API key or the call fails,
    so the pipeline never breaks in a live demo.
    """
    from llm_client import get_client_and_model

    client, model = get_client_and_model()
    if client is None:
        return None

    try:
        prompt = f"""You are mapping an unfamiliar financial data file's columns to
three canonical fields: id (a unique transaction/reference identifier),
amount (a monetary value), date (a transaction/settlement date).

Columns in this file: {columns}
Sample rows: {sample_rows}

Respond with ONLY a JSON object like:
{{"id": "<column_name>", "amount": "<column_name>", "date": "<column_name>"}}
Use the exact column names given above."""

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_TEMPERATURE_MAPPING,
        )
        text = response.choices[0].message.content.strip()
        text = re.sub(r"^```json|```$", "", text, flags=re.MULTILINE).strip()
        mapping = json.loads(text)
        # sanity check: every mapped column must actually exist in the file
        if all(v in columns for v in mapping.values()):
            return mapping
        logger.warning("LLM proposed columns not present in file, falling back to heuristic.")
    except Exception as e:
        logger.warning("LLM mapping failed (%s), falling back to heuristic.", e)
    return None


def map_schema(df: pd.DataFrame, source_name: str = "") -> dict:
    """
    Returns a dict like {"id": "txn_id", "amount": "amount_inr", "date": "payment_date"}
    mapping this dataframe's actual columns to our canonical fields.
    """
    if df is None or df.empty:
        raise ValueError(f"[{source_name}] Input file is empty — cannot map an empty schema.")
    if len(df.columns) < len(CANONICAL_FIELDS):
        raise ValueError(
            f"[{source_name}] File has only {len(df.columns)} column(s), "
            f"fewer than the {len(CANONICAL_FIELDS)} required fields — likely a malformed file."
        )

    columns = list(df.columns)
    sample_rows = df.head(3).to_dict(orient="records")

    mapping = _llm_guess(columns, sample_rows)
    if mapping is None:
        mapping = _heuristic_guess(columns)

    missing = [f for f in CANONICAL_FIELDS if f not in mapping]
    if missing:
        raise ValueError(
            f"[{source_name}] Could not confidently map fields: {missing}. "
            f"Available columns were: {columns}"
        )

    logger.info("%s mapping resolved: %s", source_name, mapping)
    return mapping


def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Returns a copy of df with canonical column names (id, amount, date)."""
    reverse = {v: k for k, v in mapping.items()}
    out = df.rename(columns=reverse)
    out = out[["id", "amount", "date"] + [c for c in out.columns if c not in CANONICAL_FIELDS]]
    out["date"] = _normalize_date_column(out["date"])
    return out


# Plausible range for Unix epoch SECONDS: year 2000 to year 2100. Real payment
# gateway APIs (Razorpay included — see created_at/settled_at in their own
# settlement recon docs) commonly report timestamps this way instead of a
# formatted date string. Without this, pandas' default datetime parser reads
# a raw epoch-seconds integer as epoch NANOSECONDS, silently producing a date
# in early 1970 and a wildly wrong multi-thousand-day gap in every comparison.
_EPOCH_SECONDS_MIN = 946684800    # 2000-01-01
_EPOCH_SECONDS_MAX = 4102444800   # 2100-01-01


def _normalize_date_column(series: pd.Series) -> pd.Series:
    """Converts a Unix epoch-seconds numeric column to real dates. Leaves
    already-string/date columns untouched."""
    numeric = pd.to_numeric(series, errors="coerce")
    looks_like_epoch_seconds = (
        numeric.notna().all()
        and (numeric.between(_EPOCH_SECONDS_MIN, _EPOCH_SECONDS_MAX)).all()
    )
    if looks_like_epoch_seconds:
        logger.info("Detected Unix epoch-seconds timestamps in date column — converting with unit='s'.")
        return pd.to_datetime(numeric, unit="s").dt.strftime("%Y-%m-%d")
    return series
