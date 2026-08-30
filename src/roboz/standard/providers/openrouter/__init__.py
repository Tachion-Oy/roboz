"""OpenRouter client configuration, model inventory, and catalog."""

from __future__ import annotations

from enum import StrEnum

from openai import OpenAI

from roboz.llm import LLMEndpoint
from roboz.runtime import load_key

from roboz.llm.providers.catalog import ProviderCatalog
from roboz.llm.providers.model_types import ChatModelSpec

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterApiKey(StrEnum):
    """Environment variables used by the OpenRouter integration."""

    API_KEY = "OPENROUTER_API_KEY"


def openrouter_client(api_key: str) -> OpenAI:
    """Build an OpenAI-compatible client configured for OpenRouter."""

    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def openrouter_model_name(model: ChatModelSpec) -> str:
    """Select OpenRouter's throughput-optimized route for a model."""

    return f"{model.model_id}:nitro"


def openrouter_endpoint(model: ChatModelSpec, model_name: str) -> LLMEndpoint:
    """Materialize one OpenRouter endpoint, loading credentials on demand."""

    return LLMEndpoint(
        client=openrouter_client(load_key(OpenRouterApiKey.API_KEY)),
        model_name=model_name,
        api_name="openrouter",
        max_context_tokens=model.max_context_tokens,
        stream=True,
    )


OPENROUTER_MODELS: tuple[ChatModelSpec, ...] = (
    ChatModelSpec("z-ai/glm-5.3-flash", 1_310_720),
)


class OpenRouterCatalog(ProviderCatalog):
    """OpenRouter chat-model catalog."""

    def __init__(self) -> None:
        super().__init__(
            api_name="openrouter",
            endpoint_factory=openrouter_endpoint,
            models=OPENROUTER_MODELS,
            model_name_factory=openrouter_model_name,
        )


openrouter = OpenRouterCatalog()

__all__ = [
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODELS",
    "OpenRouterApiKey",
    "OpenRouterCatalog",
    "openrouter",
    "openrouter_client",
    "openrouter_endpoint",
    "openrouter_model_name",
]
