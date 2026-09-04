"""
llm_client.py
-------------
Single shared place that decides which LLM provider to use, so
schema_mapper.py, explainer.py, and app.py don't each duplicate this logic.

Supports two providers, both through the same OpenAI-compatible client:
  - Groq (recommended: free, no card needed, very fast) -> set GROQ_API_KEY
  - OpenAI (paid) -> set OPENAI_API_KEY

If GROQ_API_KEY is set, it's used first. If neither is set, get_client()
returns None and every calling module falls back to its heuristic/template
logic automatically — the pipeline never breaks from a missing key.
"""

import os
from dotenv import load_dotenv
from logger import get_logger

# Ensure .env is loaded even if this module is imported before config.py.
load_dotenv()

logger = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Groq deprecated llama-3.3-70b-versatile in June 2026. Using their recommended
# replacement — a strong, fast general-purpose model well suited to this task.
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


def get_client_and_model():
    """
    Returns (client, model_name) for whichever provider has a key set,
    or (None, None) if no key is configured — callers must handle that
    by falling back to their non-LLM logic.
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    try:
        from openai import OpenAI  # the Groq API is OpenAI-compatible

        if groq_key:
            logger.info("Using Groq as the LLM provider.")
            client = OpenAI(api_key=groq_key, base_url=GROQ_BASE_URL)
            model = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
            return client, model

        if openai_key:
            logger.info("Using OpenAI as the LLM provider.")
            client = OpenAI(api_key=openai_key)
            model = os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
            return client, model

    except Exception as e:
        logger.warning("Could not initialize LLM client (%s) — using fallback logic.", e)

    logger.info("No LLM API key found — running in heuristic/template fallback mode.")
    return None, None
