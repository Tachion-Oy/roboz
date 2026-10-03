"""Opt-in tagged CLI v2 with guarded transfers and read commands; opt in through this package."""

from .command import get_run_tagged_file_command
from .contracts import (
    CommandName,
    ControlOperator,
    TaggedFileCommand,
    TaggedToken,
    TokenTag,
)

__all__ = [
    "CommandName",
    "ControlOperator",
    "TaggedFileCommand",
    "TaggedToken",
    "TokenTag",
    "get_run_tagged_file_command",
]
