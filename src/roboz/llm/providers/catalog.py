"""Provider-neutral machinery for lazy, discoverable model catalogs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import RLock
from types import MappingProxyType
from typing import Mapping

from roboz.llm import LLMEndpoint
from roboz.tooling import ExternalDependencyKind, LazyExternalDependency

from roboz.llm.providers.model_types import ChatModelSpec

EndpointFactory = Callable[[ChatModelSpec, str], LLMEndpoint]
ModelNameFactory = Callable[[ChatModelSpec], str]


def model_attribute_name(model_id: str) -> str:
    """Map a provider model id to its stable Python attribute name."""

    return (
        model_id.replace("/", "__")
        .replace("-", "_")
        .replace(".", "_")
        .replace(":", "_")
    )


def model_id_as_name(model: ChatModelSpec) -> str:
    """Use the provider's model identifier unchanged at runtime."""

    return model.model_id


class ProviderCatalog:
    """A discoverable model catalog whose endpoints are built on first use.

    Model specs remain immutable and provider-neutral. Accessing a model
    attribute returns a lazy external dependency; the provider-owned endpoint
    factory is called only when that dependency is materialized.
    """

    def __init__(
        self,
        *,
        api_name: str,
        endpoint_factory: EndpointFactory,
        models: Sequence[ChatModelSpec],
        model_name_factory: ModelNameFactory = model_id_as_name,
    ) -> None:
        self.api_name = api_name
        self.endpoint_factory = endpoint_factory
        self.models = tuple(models)
        self.model_name_factory = model_name_factory

        models_by_attribute = {
            model_attribute_name(model.model_id): model for model in self.models
        }
        if len(models_by_attribute) != len(self.models):
            raise ValueError(f"{api_name!r} has colliding model attribute names")
        self.models_by_attribute: Mapping[str, ChatModelSpec] = MappingProxyType(
            models_by_attribute
        )
        self._dependencies: dict[str, LazyExternalDependency[LLMEndpoint]] = {}
        self._endpoints: dict[str, LLMEndpoint] = {}
        self._lock = RLock()

    def __getattr__(self, name: str) -> LazyExternalDependency[LLMEndpoint]:
        return self._dependency(name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(self.models_by_attribute))

    def _dependency(self, name: str) -> LazyExternalDependency[LLMEndpoint]:
        with self._lock:
            cached = self._dependencies.get(name)
            if cached is not None:
                return cached

            model = self._model_spec(name)
            model_name = self.model_name_factory(model)
            dependency = LazyExternalDependency(
                dependency_id_value=f"model:{self.api_name}:{model_name}",
                dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
                metadata={
                    "api_name": self.api_name,
                    "model_name": model_name,
                    "endpoint_type": model.endpoint_type,
                },
                resolver=lambda: self._materialize(name),
            )
            self._dependencies[name] = dependency
            return dependency

    def _model_spec(self, name: str) -> ChatModelSpec:
        try:
            return self.models_by_attribute[name]
        except KeyError:
            available = ", ".join(self.models_by_attribute)
            raise AttributeError(
                f"{self.api_name!r} has no model {name!r}. Available: {available}"
            ) from None

    def _materialize(self, name: str) -> LLMEndpoint:
        with self._lock:
            cached = self._endpoints.get(name)
            if cached is not None:
                return cached

            model = self._model_spec(name)
            model_name = self.model_name_factory(model)
            endpoint = self.endpoint_factory(model, model_name)
            self._endpoints[name] = endpoint
            return endpoint


__all__ = [
    "EndpointFactory",
    "ModelNameFactory",
    "ProviderCatalog",
    "model_attribute_name",
    "model_id_as_name",
]
