"""Field annotations for runtime metadata retained outside agent payloads."""

from pydantic import BaseModel
from pydantic.json_schema import SkipJsonSchema


class RuntimeOnly(SkipJsonSchema):
    """Hide a field from agent schemas and identify it for payload exclusion.

    Normal validation and serialization retain the field so runtime consumers
    and persistence can still use its value.
    """


def runtime_fields(model: type[BaseModel]) -> set[str]:
    """Return fields marked as runtime metadata, including inherited fields."""
    return {
        name
        for name, field in model.model_fields.items()
        if any(isinstance(marker, RuntimeOnly) for marker in field.metadata)
    }
