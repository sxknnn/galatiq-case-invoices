"""Creates the model used by the agents."""
import os

from langchain.chat_models import init_chat_model

import config

# Which key each provider needs. None means no key is needed.
PROVIDER_KEYS = {
    "xai": "XAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": None,
}


def get_llm():
    """Return the configured model, or None to run the offline simulation."""
    provider = config.MODEL.split(":", 1)[0]
    key_name = PROVIDER_KEYS.get(provider)
    if key_name and not os.environ.get(key_name):
        print(f"No {key_name} found: running the offline simulation, not {config.MODEL}.")
        return None
    return init_chat_model(config.MODEL, temperature=0)