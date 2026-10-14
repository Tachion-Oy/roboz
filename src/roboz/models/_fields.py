"""Field annotations for runtime metadata retained outside agent payloads."""

from pydantic import BaseModel
from pydantic.json_schema import SkipJsonSchema


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


class RuntimeModel(BaseModel):
    """Preserve runtime classification when subclasses redeclare field defaults."""

    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        """Restore runtime markers after field annotations have been resolved."""
        super().__pydantic_on_complete__()
        changed = False
        for name in runtime_fields(cls):
            field = cls.model_fields.get(name)
            if field is not None and not any(
                isinstance(marker, RuntimeOnly) for marker in field.metadata
            ):
                field.metadata = [*field.metadata, RuntimeOnly()]
                changed = True
        if changed:
            # Pydantic already built the schema before calling this hook.
            cls.model_rebuild(force=True)
