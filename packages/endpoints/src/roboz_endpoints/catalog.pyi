"""Generated types for Pylance; DO NOT EDIT.

Source: packages/endpoints/src/roboz_endpoints/inventory.py
Regenerate: uv run python scripts/generate_endpoint_catalog.py
Check: uv run python scripts/generate_endpoint_catalog.py --check
"""

from collections.abc import Mapping
from typing import Self
from roboz_endpoints.adapters.openai_compatible import OpenAICompatibleAdapter
from roboz_endpoints.specs import ModelSpec
from roboz_endpoints.inventory import openrouter as openrouter
from roboz_endpoints.inventory import cerebras as cerebras
from roboz_endpoints.inventory import groq as groq

class Catalog[Spec: ModelSpec]:
    """A collection of model specifications and independently cached concrete endpoints."""
    api_name: str
    models: tuple[Spec, ...]
    models_by_attribute: Mapping[str, Spec]
    _adapter: OpenAICompatibleAdapter
    _stream: bool

    def __init__(self, *, adapter: OpenAICompatibleAdapter, models: Mapping[str, Spec], stream: bool = True) -> None:
        """Configure a collection without loading SDKs or reading credentials."""
        ...

    def configured(self, *, api_key: str | None = None, timeout_s: float = 60.0, stream: bool = True) -> Self:
        """Create an independent collection with these settings and fresh caches.

        Provider settings and model data are retained. Omitted options use the
        defaults shown here, including environment-based credentials.
        """
        ...

    def __dir__(self) -> list[str]:
        """Include inventory model names without selecting their dependencies."""
        ...
