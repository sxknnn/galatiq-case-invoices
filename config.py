"""Project settings. Change the MODEL line to swap models."""
from dotenv import load_dotenv

load_dotenv()

# Models
MODEL = "xai:grok-4.7"
# MODEL = "openai:your-model"
# MODEL = "ollama:llama3.2"

# Workflow
LEDGER_DATABASE = "data/autovoice.db"
REVIEW_LIMIT_USD = 10_000
EXTRACTION_TRIES = 3
AUDIT_LOG_FILE = "logs/audit.jsonl"
