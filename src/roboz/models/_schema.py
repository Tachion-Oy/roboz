from __future__ import annotations

import types
from dataclasses import fields, is_dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from roboz.models._telemetry import NonAgentFacingFields
from roboz.models.core import Message


def get_constituent_types(type_: type) -> tuple[type, ...]:
    """Return constituent types for validation: union members or a single type as tuple."""
    origin = get_origin(type_)
    if origin in (types.UnionType, Union):
        return get_args(type_)
    return (type_,)


def _collect_type_names(annotation: Any, names: set[str]) -> None:
    """Recursively collect type names from an annotation for schema scrubbing."""
    if annotation is None or isinstance(annotation, str):
        return
    origin = get_origin(annotation)
    if origin:
        for arg in get_args(annotation):
            if arg and hasattr(arg, "__name__"):
                names.add(arg.__name__)
                names.add(arg.__name__.lower())
            _collect_type_names(arg, names)
    elif annotation and hasattr(annotation, "__name__"):
        names.add(annotation.__name__)
        names.add(annotation.__name__.lower())
    if is_dataclass(annotation):
        for f in fields(annotation):
            _collect_type_names(f.type, names)


def extract_fields_and_type_names(
    model: type[BaseModel] = NonAgentFacingFields,
) -> tuple[frozenset[str], frozenset[str]]:
    names: set[str] = set()

    for field in model.model_fields.values():
        annotation = field.annotation
        if annotation:
            _collect_type_names(annotation, names)

    return frozenset(model.model_fields), frozenset(names)


def schema_scrubber(
    schema: dict, model: type[BaseModel] = NonAgentFacingFields
) -> dict:
    """Remove internal-only fields from a Pydantic JSON schema before exposing to agent."""
    fields, defs = extract_fields_and_type_names(model)
    if "properties" in schema:
        for f in fields:
            schema["properties"].pop(f, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in fields]
    if "$defs" in schema:
        for k in defs:
            schema["$defs"].pop(k, None)
        if not schema["$defs"]:
            del schema["$defs"]
    return schema


def messages_scrubber(messages: list[Message]):
    allowed = set(
        schema_scrubber(Message.model_json_schema()).get("properties", {}).keys()
    )
    if not allowed:
        raise ValueError(f"No allowed fields in message schema {Message=}")
    return [
        {k: v for k, v in m.model_dump(mode="json").items() if k in allowed}
        for m in messages
    ]
