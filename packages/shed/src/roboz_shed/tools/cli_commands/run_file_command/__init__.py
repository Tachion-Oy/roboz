"""File-oriented guarded CLI tool factory and specs."""

from .command import get_run_file_command, resolve_input
from .specs import FILE_COMMANDS_DELETE, FILE_COMMANDS_READ, FILE_COMMANDS_WRITE

__all__ = [
    "FILE_COMMANDS_DELETE",
    "FILE_COMMANDS_WRITE",
    "FILE_COMMANDS_READ",
    "get_run_file_command",
    "resolve_input",
]
