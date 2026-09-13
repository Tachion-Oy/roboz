"""Lazy model collections configured by inventory data.

Generated typing declarations expose each inventory collection's model names
and endpoint types to editors without introducing provider-specific classes.
"""

from collections.abc import Mapping
from keyword import iskeyword
from threading import Lock
from types import MappingProxyType
from typing import Self

from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz_endpoints.adapters.openai_compatible import OpenAICompatibleAdapter
from roboz_endpoints.specs import ChatModelSpec, ModelSpec


class Catalog[Spec: ModelSpec]:
    """A collection of model specifications and independently cached concrete endpoints."""

    def __init__(
        self,
        *,
        adapter: OpenAICompatibleAdapter,
        models: Mapping[str, Spec],
        stream: bool = True,
    ) -> None:
        """Configure a collection without loading SDKs or reading credentials."""
        self.api_name = adapter.api_name
        self.models_by_attribute: Mapping[str, Spec] = MappingProxyType(dict(models))
        self.models = tuple(self.models_by_attribute.values())
        self._adapter = adapter
        self._stream = stream
        self._lock = Lock()
        for name in models:
            if (
                not name.isidentifier()
                or name.startswith("_")
                or iskeyword(name)
                or name in self.__dict__
                or hasattr(type(self), name)
            ):
                raise ValueError(f"Invalid model attribute: {name!r}")

    def configured(
        self,
        *,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        stream: bool = True,
    ) -> Self:
        """Create an independent collection with these settings and fresh caches.

        Provider settings and model data are retained. Omitted options use the
        defaults shown here, including environment-based credentials.
        """
        return type(self)(
            adapter=self._adapter.configured(api_key=api_key, timeout_s=timeout_s),
            models=self.models_by_attribute,
            stream=stream,
        )

    def __getattr__(
        self, name: str
    ) -> LLMEndpoint | TranscriptionEndpoint:
        """Select and cache a concrete endpoint according to its model specification."""
        try:
            model = self.models_by_attribute[name]
        except KeyError:
            raise AttributeError(name) from None
        with self._lock:
            if name not in self.__dict__:
                if isinstance(model, ChatModelSpec):
                    dependency = self._adapter.chat_endpoint(
                        model=model.model_id,
                        max_context_tokens=model.max_context_tokens,
                        stream=self._stream,
                    )
                else:
                    dependency = self._adapter.transcription_endpoint(model=model.model_id)
                self.__dict__[name] = dependency
            return self.__dict__[name]

    def __dir__(self) -> list[str]:
        """Include inventory model names without selecting their dependencies."""
        return sorted(set(super().__dir__()) | self.models_by_attribute.keys())


def __getattr__(name: str):
    """Keep named collection imports from this module compatible."""
    if name.startswith("_") and name != "__all__":
        raise AttributeError(name)
    from roboz_endpoints.inventory import CATALOGS

    if name == "__all__":
        return ["Catalog", *CATALOGS]
    try:
        return CATALOGS[name]
    except KeyError:
        raise AttributeError(name) from None
