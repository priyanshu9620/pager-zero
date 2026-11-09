"""The ONLY module that knows about LLM providers.

Everything else calls `get_llm()` and gets a LangChain `BaseChatModel`. Swapping Claude for
Gemini / Groq / Ollama is two lines in `.env` (see README → "Choosing a model").

Rules the rest of the codebase follows so this stays true (notes D-3):
  * only tool-calling, plain text and `with_structured_output()` — features every provider has
  * no provider-specific kwargs here or anywhere else
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from pager_zero.config import settings

# Provider id -> env var that must hold the key (None = no key needed).
PROVIDER_KEY_ENV: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": None,
}


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}  # SDKs back off on 429/5xx
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)
