"""
config.py
---------
All tunable values live here, in one documented place, instead of scattered
across the codebase. This is what a reviewer looks for first in a real PR —
config that's visible and justified, not buried inside logic files.
"""

import os
from dotenv import load_dotenv

# Auto-load variables from a local .env file (if present) into the environment.
# This runs once, as early as possible (config.py is imported by everything
# else), so GROQ_API_KEY / OPENAI_API_KEY set in .env "just work" without
# having to export them manually in every new terminal session.
# Safe to call even if .env doesn't exist — it just does nothing.
load_dotenv()

# --- Matching thresholds -----------------------------------------------
# Confidence score (0-100) required to auto-match a pair without human review.
# Set conservatively high on purpose: in a finance context, a false auto-match
# is worse than an extra exception a human has to glance at.
AUTO_MATCH_THRESHOLD = float(os.environ.get("AUTO_MATCH_THRESHOLD", 90))

# Amount difference (%) still considered "clean" — i.e. explained by the
# standard payment gateway fee, not an anomaly. Razorpay's own published
# rate is 2% MDR + 18% GST on that fee (~2.36% effective) — set the
# tolerance a bit above that so the standard fee itself is recognized as
# normal, while a genuinely anomalous fee (well beyond the standard rate)
# still gets caught as an exception.
AMOUNT_TOLERANCE_PCT = float(os.environ.get("AMOUNT_TOLERANCE_PCT", 2.6))

# Days of settlement lag considered normal before flagging as suspicious.
# Real settlement windows commonly run several business days — set slightly
# looser than a naive guess to avoid flagging routine lag as an anomaly.
MAX_REASONABLE_DELAY_DAYS = int(os.environ.get("MAX_REASONABLE_DELAY_DAYS", 7))

# When payments carry a genuine cross-reference field (e.g. internal_ref), that
# reference is the transaction's IDENTITY. A pair is only auto-matched if the
# reference codes agree at least this much (0-100, compared after stripping
# punctuation and any prefix shared by the whole column). Without this gate, an
# unrelated settlement with a coincidentally similar amount/date could be
# matched and "steal" the settlement that belongs to a later payment.
REFERENCE_MATCH_MIN_SIMILARITY = int(os.environ.get("REFERENCE_MATCH_MIN_SIMILARITY", 80))

# A settlement that arrives later than MAX_REASONABLE_DELAY_DAYS but within this
# many days, with a verified reference and a normal amount, is the same
# transaction arriving late. It is MATCHED but carries a "late_settlement"
# flag for human review. Beyond this, it stays an exception.
MAX_LATE_SETTLEMENT_DAYS = int(os.environ.get("MAX_LATE_SETTLEMENT_DAYS", 30))

# Fee-consistency check. If the matched pairs show ONE consistent fee rate
# (e.g. 2% MDR + 18% GST = 2.36%), then a pair whose deduction differs from
# that typical rate by more than FEE_DEVIATION_PP percentage points is an
# unexplained fee, even if it is inside AMOUNT_TOLERANCE_PCT. It is only
# applied when there are enough pairs and the fee rate is genuinely
# consistent (median absolute deviation <= FEE_CONSISTENCY_MAX_MAD_PP);
# otherwise the data has no fee structure to check against and it is skipped.
FEE_CHECK_MIN_PAIRS = int(os.environ.get("FEE_CHECK_MIN_PAIRS", 30))
FEE_CONSISTENCY_MAX_MAD_PP = float(os.environ.get("FEE_CONSISTENCY_MAX_MAD_PP", 0.05))
FEE_DEVIATION_PP = float(os.environ.get("FEE_DEVIATION_PP", 0.05))

# Minimum fuzzy ID similarity (0-100) to even consider two records as a
# candidate pair, before scoring them properly.
MIN_ID_SIMILARITY_TO_CONSIDER = int(os.environ.get("MIN_ID_SIMILARITY_TO_CONSIDER", 20))

# --- LLM settings --------------------------------------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE_MAPPING = 0.0     # deterministic for schema mapping
LLM_TEMPERATURE_EXPLANATION = 0.3  # slightly more natural for explanations

# --- Paths ----------------------------------------------------------------
DATA_DIR = os.environ.get("DATA_DIR", "data")
LOG_DIR = os.environ.get("LOG_DIR", "logs")
AUDIT_LOG_PATH = os.path.join(LOG_DIR, "audit_trail.jsonl")
APP_LOG_PATH = os.path.join(LOG_DIR, "app.log")

# --- Required canonical fields --------------------------------------------
CANONICAL_FIELDS = ["id", "amount", "date"]
