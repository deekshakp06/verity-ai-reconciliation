"""Run the test suite offline: blank API keys BEFORE any module loads .env.
load_dotenv() never overrides variables that already exist, so tests always use
the deterministic template path (fast, free, no network, same result every run)."""
import os

os.environ["GROQ_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""