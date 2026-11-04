"""Project settings. Change the MODEL line to swap models."""
from dotenv import load_dotenv

load_dotenv()

# Models
MODEL = "xai:grok-3"
# MODEL = "openai:your-model"
# MODEL = "ollama:llama3.2"

# Workflow
DB_PATH = "data/acme.db"
APPROVAL_THRESHOLD = 10_000
MAX_REVIEW_ROUNDS = 2
MAX_EXTRACTION_ATTEMPTS = 3