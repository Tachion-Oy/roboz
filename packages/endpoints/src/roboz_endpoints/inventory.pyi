"""Generated types for Pylance; DO NOT EDIT.

Source: packages/endpoints/src/roboz_endpoints/inventory.py
Regenerate: uv run python scripts/generate_endpoint_catalog.py
Check: uv run python scripts/generate_endpoint_catalog.py --check
"""

from roboz import LazyExternalDependency
from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz_endpoints.catalog import Catalog
from roboz_endpoints.specs import (
    ChatModelSpec as ChatModelSpec,
    ModelSpec as ModelSpec,
    TranscriptionModelSpec as TranscriptionModelSpec,
)

CATALOGS: dict[str, Catalog[ChatModelSpec] | Catalog[TranscriptionModelSpec] | Catalog[ModelSpec]]

class _openrouter_Catalog(Catalog[ChatModelSpec]):
    z_ai__glm_5_3: LazyExternalDependency[LLMEndpoint]
    z_ai__glm_5_3_flash: LazyExternalDependency[LLMEndpoint]

openrouter: _openrouter_Catalog
OPENROUTER_MODELS: tuple[ChatModelSpec, ...]

class _cerebras_Catalog(Catalog[ChatModelSpec]):
    gpt_oss_120b: LazyExternalDependency[LLMEndpoint]

cerebras: _cerebras_Catalog
CEREBRAS_MODELS: tuple[ChatModelSpec, ...]

class _groq_Catalog(Catalog[TranscriptionModelSpec]):
    whisper_large_v3_turbo: LazyExternalDependency[TranscriptionEndpoint]

groq: _groq_Catalog
GROQ_MODELS: tuple[TranscriptionModelSpec, ...]

__all__ = ['CATALOGS', 'ChatModelSpec', 'ModelSpec', 'TranscriptionModelSpec', 'openrouter', 'cerebras', 'groq', 'OPENROUTER_MODELS', 'CEREBRAS_MODELS', 'GROQ_MODELS']
