"""Shared catalogue declarations and self-contained user inventory generation."""

from collections.abc import Mapping
import pprint
from textwrap import dedent, indent
from types import ModuleType
from typing import TypeAliasType

import roboz
import roboz.llm as llm
from roboz_endpoints import catalog, specs
from roboz_endpoints.adapters import openai_compatible

from roboz_endpoints._inventory_codec import (
    ADAPTER_FIELDS,
    DATA_NAME,
    MARKER,
    MODEL_KIND_FIELD,
    MODEL_SPEC_TYPES,
    Inventory,
    document_from_inventory,
)
from roboz_endpoints.specs import ModelSpec


# The association between a record and its endpoint is explicit. Names and
# discriminator values are supplied by the actual classes, not duplicated text.
_ENDPOINT_TYPES = {
    specs.ChatModelSpec: llm.LLMEndpoint,
    specs.TranscriptionModelSpec: llm.TranscriptionEndpoint,
}


def spec_type_name(models: Mapping[str, ModelSpec]) -> str:
    """Describe homogeneous collections precisely, and mixed or empty ones broadly."""
    names = {type(model).__name__ for model in models.values()}
    return next(iter(names)) if len(names) == 1 else ModelSpec.__name__


def collection_declaration(
    name: str, models: Mapping[str, ModelSpec], *, prefix: str = ""
) -> list[str]:
    """Render the same precise collection declaration for stubs and user modules."""
    class_name = f"_{name}_Catalog"
    base = f"{prefix}{catalog.Catalog.__name__}[{prefix}{spec_type_name(models)}]"
    lines = [f"class {class_name}({base}):"]
    for attribute, model in models.items():
        endpoint = _ENDPOINT_TYPES[type(model)].__name__
        dependency = roboz.LazyExternalDependency.__name__
        lines.append(f"    {attribute}: {prefix}{dependency}[{prefix}{endpoint}]")
    if not models:
        lines.append("    ...")
    return [*lines, "", f"{name}: {class_name}"]


def _alias(symbol: type | TypeAliasType) -> str:
    return f"_{symbol.__name__}"


def _import_from(module: ModuleType, symbol: type | TypeAliasType) -> str:
    """Import a real symbol through its public module using a private local alias."""
    return f"from {module.__name__} import {symbol.__name__} as {_alias(symbol)}"


def _type_declarations(inventory: Inventory) -> str:
    imports = [
        _import_from(roboz, roboz.LazyExternalDependency),
        *(_import_from(llm, endpoint) for endpoint in _ENDPOINT_TYPES.values()),
        _import_from(specs, ModelSpec),
    ]
    collections = [
        "\n".join(collection_declaration(name, provider.models, prefix="_"))
        for name, provider in inventory.items()
    ]
    return "\n\n".join(["\n".join(imports), *collections])


def _runtime_construction() -> str:
    """Construct the embedded snapshot using spec constructors and declared settings."""
    spec_types = ", ".join(
        f"{_alias(spec)}.{MODEL_KIND_FIELD}: {_alias(spec)}"
        for spec in MODEL_SPEC_TYPES
    )
    return dedent(f"""\
        _namespace = globals()
        _spec_types = {{{spec_types}}}
        for _name, _provider in {DATA_NAME}['providers'].items():
            _models = {{}}
            for _attribute, _record in _provider['models'].items():
                _values = _record.copy()
                _spec = _spec_types[_values.pop({MODEL_KIND_FIELD!r})]
                _models[_attribute] = _spec(**_values)
            _settings = {{_field: _provider[_field] for _field in {ADAPTER_FIELDS!r}}}
            _namespace[_name] = {_alias(catalog.Catalog)}(
                adapter={_alias(openai_compatible.OpenAICompatibleAdapter)}(api_name=_name, **_settings),
                models=_models, stream=_provider['stream'],
            )
        """).rstrip()


def render_module(inventory: Inventory) -> str:
    """Generate runtime data and static declarations together without client creation."""
    imports = [
        "from typing import TYPE_CHECKING as _TYPE_CHECKING",
        _import_from(catalog, catalog.Catalog),
        _import_from(openai_compatible, openai_compatible.OpenAICompatibleAdapter),
        *(_import_from(specs, spec) for spec in MODEL_SPEC_TYPES),
    ]
    data = pprint.pformat(
        document_from_inventory(inventory), sort_dicts=False, width=100
    )
    source = (
        "\n\n".join(
            [
                MARKER
                + '\n"""Project model snapshot. Edit its JSON source and re-run inventory import."""',
                "\n".join(imports),
                f"{DATA_NAME} = {data}",
                "if _TYPE_CHECKING:\n"
                + indent(_type_declarations(inventory), "    ")
                + "\nelse:\n"
                + indent(_runtime_construction(), "    "),
                f"__all__ = {list(inventory)!r}",
            ]
        )
        + "\n"
    )
    compile(source, "<generated inventory>", "exec")
    return source
