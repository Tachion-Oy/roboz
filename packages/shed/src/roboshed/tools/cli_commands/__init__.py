"""Guarded Unix file-oriented CLI tools (read, locate, modify files) and shared CLI utilities."""

from roboshed.models import CommandReady, ParseError
from roboshed.tools.guard import operation_guard
from roboshed.tools.types import ResolvedFileCommand

from .utilities.cmd_spec import CmdSpec
from .utilities.formatting import (
    format_cli_commands_help,
    format_cli_constraints,
    format_cli_full_help,
)

__all__ = [
    "CmdSpec",
    "CommandReady",
    "ParseError",
    "ResolvedFileCommand",
    "format_cli_commands_help",
    "format_cli_constraints",
    "format_cli_full_help",
    "operation_guard",
]
