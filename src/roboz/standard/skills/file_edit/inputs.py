"""Agent-facing input for guarded file editing."""

from pydantic import ConfigDict, Field
from roboz.models import Empty


class ApplyPatch(Empty):
    """Single-file apply_patch input (literal find/replace)."""

    path: str = Field(
        ...,
        description=(
            "Single file path to edit (relative to the tool base or absolute). "
            "Permission rules may still be configured as relative or absolute patterns."
        ),
    )
    old_string: str = Field(
        ...,
        description=(
            "Exact substring to find in the file. "
            "Use empty string to rewrite the full file with new_string."
        ),
    )
    new_string: str = Field(
        ...,
        description="Replacement text (may be empty to delete the matched substring)",
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "If false: old_string must occur exactly once. "
            "If true: replace every occurrence (at least one required)."
        ),
    )
    model_config = ConfigDict(extra="forbid")


__all__ = ["ApplyPatch"]
