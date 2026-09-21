"""Generated types for Pylance; DO NOT EDIT.

Source: src/roboz/endpoints/inventory.py
Regenerate: uv run python scripts/generate_endpoint_catalog.py
Check: uv run python scripts/generate_endpoint_catalog.py --check
"""

from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz.endpoints.catalog import Catalog
from roboz.endpoints.specs import (
    ChatModelSpec as ChatModelSpec,
    ModelSpec as ModelSpec,
    TranscriptionModelSpec as TranscriptionModelSpec,
)

CATALOGS: dict[str, Catalog[ChatModelSpec] | Catalog[TranscriptionModelSpec] | Catalog[ModelSpec]]

class _openrouter_Catalog(Catalog[ChatModelSpec]):
    z_ai__glm_5_3: LLMEndpoint
    z_ai__glm_5_3_flash: LLMEndpoint

openrouter: _openrouter_Catalog
OPENROUTER_MODELS: tuple[ChatModelSpec, ...]

class _cerebras_Catalog(Catalog[ChatModelSpec]):
    gpt_oss_120b: LLMEndpoint

cerebras: _cerebras_Catalog
CEREBRAS_MODELS: tuple[ChatModelSpec, ...]

class _groq_Catalog(Catalog[TranscriptionModelSpec]):
    whisper_large_v3_turbo: TranscriptionEndpoint

groq: _groq_Catalog
GROQ_MODELS: tuple[TranscriptionModelSpec, ...]

__all__ = ['CATALOGS', 'ChatModelSpec', 'ModelSpec', 'TranscriptionModelSpec', 'openrouter', 'cerebras', 'groq', 'OPENROUTER_MODELS', 'CEREBRAS_MODELS', 'GROQ_MODELS']
