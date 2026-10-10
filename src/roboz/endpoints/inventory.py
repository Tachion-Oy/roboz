"""Provider settings and model data for the public collections.

Context limits are fixed configuration values, not live provider discovery.
After editing, run ``uv run python scripts/generate_endpoint_catalog.py`` and
commit the generated typing declarations alongside this file.
"""

from roboz.endpoints.adapters.openai_compatible import OpenAICompatibleAdapter
from roboz.endpoints.catalog import Catalog
from roboz.endpoints.specs import (
    ChatModelSpec,
    ModelSpec,
    TranscriptionModelSpec,
)

CATALOGS = {
    collection.api_name: collection
    for collection in (
        Catalog(
            adapter=OpenAICompatibleAdapter(
                api_name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
            ),
            models={
                "z_ai__glm_5_3": ChatModelSpec("z-ai/glm-5.3", 1_310_720),
                "z_ai__glm_5_3_flash": ChatModelSpec("z-ai/glm-5.3-flash", 1_310_720),
            },
        ),
        Catalog(
            adapter=OpenAICompatibleAdapter(
                api_name="cerebras",
                base_url="https://api.cerebras.ai/v1",
                api_key_env="CEREBRAS_API_KEY",
            ),
            models={
                "gpt_oss_120b": ChatModelSpec("gpt-oss-120b", 131_072),
            },
        ),
        Catalog(
            adapter=OpenAICompatibleAdapter(
                api_name="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key_env="GROQ_API_KEY",
            ),
            models={
                "whisper_large_v3_turbo": TranscriptionModelSpec("whisper-large-v3-turbo"),
            },
        ),
    )
}

globals().update(CATALOGS)
globals().update({f"{name.upper()}_MODELS": c.models for name, c in CATALOGS.items()})

__all__ = [
    "CATALOGS",
    "ChatModelSpec",
    "ModelSpec",
    "TranscriptionModelSpec",
    *CATALOGS,
    *(f"{name.upper()}_MODELS" for name in CATALOGS),
]
