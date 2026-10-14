"""Field annotations for runtime metadata retained outside agent payloads."""

from pydantic import BaseModel, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue, SkipJsonSchema
from pydantic_core import CoreSchema


class RuntimeOnly(SkipJsonSchema):
    """Hide a field from agent schemas and identify it for payload exclusion.

    Normal validation and serialization retain the field so runtime consumers
    and persistence can still use its value.
    """


def runtime_fields(model: type[BaseModel]) -> set[str]:
    """Return runtime fields declared anywhere in the model's inheritance chain."""
    return {
        name
        for base in model.__mro__
        if issubclass(base, BaseModel)
        for name, field in base.model_fields.items()
        if any(isinstance(marker, RuntimeOnly) for marker in field.metadata)
    }


def _without_runtime_fields(schema: CoreSchema, excluded: set[str]) -> CoreSchema:
    """Copy the model's schema wrappers and omit runtime fields for JSON Schema."""
    if schema["type"] == "model-fields":
        return {
            **schema,
            "fields": {
                name: field for name, field in schema["fields"].items()
                if name not in excluded
            },
        }
    if (
        schema["type"] == "model"
        or schema["type"] == "function-before"
        or schema["type"] == "function-after"
        or schema["type"] == "function-wrap"
    ):
        result = schema.copy()
        result["schema"] = _without_runtime_fields(schema["schema"], excluded)
        return result
    return schema


class RuntimeModel(BaseModel):
    """Keep inherited runtime fields out of generated JSON schemas."""

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Omit runtime fields without changing cached validation or serialization."""
        return super().__get_pydantic_json_schema__(
            _without_runtime_fields(core_schema, runtime_fields(cls)), handler,
        )
