"""Opt-in tagged CLI v2 with guarded discovery, reads, writes, transfers, and deletion."""

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
