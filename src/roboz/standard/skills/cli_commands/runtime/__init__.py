"""Shared runtime for guarded command and file-operation tools."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .guard import (
        build_guarded_tool_chain as build_guarded_tool_chain,
        guard_operation as guard_operation,
        resolve_allow_verdict as resolve_allow_verdict,
    )
    from .runner import (
        ExecutableCommandCatalog as ExecutableCommandCatalog,
        PopenStreamedProcessRunner as PopenStreamedProcessRunner,
        StreamedProcessRunner as StreamedProcessRunner,
        execute_file_command as execute_file_command,
        run_cli_argv as run_cli_argv,
        run_cli_argv_streamed as run_cli_argv_streamed,
    )
    from .truncation import default_cli_truncation as default_cli_truncation

_EXPORT_MODULES = {
    "ExecutableCommandCatalog": ".runner",
    "PopenStreamedProcessRunner": ".runner",
    "StreamedProcessRunner": ".runner",
    "build_guarded_tool_chain": ".guard",
    "default_cli_truncation": ".truncation",
    "execute_file_command": ".runner",
    "guard_operation": ".guard",
    "resolve_allow_verdict": ".guard",
    "run_cli_argv": ".runner",
    "run_cli_argv_streamed": ".runner",
}


def __getattr__(name: str) -> Any:
    """Load public runtime exports without introducing package import cycles."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "ExecutableCommandCatalog",
    "PopenStreamedProcessRunner",
    "StreamedProcessRunner",
    "build_guarded_tool_chain",
    "default_cli_truncation",
    "execute_file_command",
    "guard_operation",
    "resolve_allow_verdict",
    "run_cli_argv",
    "run_cli_argv_streamed",
]
