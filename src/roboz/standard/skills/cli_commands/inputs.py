"""Agent-facing inputs for guarded CLI commands."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema
from roboz.models import Empty


class RunFileCommand(BaseModel):
    """One step in a ``run_file_command`` tool invocation."""

    command: str = Field(
        ...,
        description="Unix command: grep, cat, find, ls, tee, wc, git, etc.",
    )
    argv: list[str] = Field(
        default_factory=list,
        description="All command arguments in order (subprocess argv[1:]). Includes flags, positionals, and path-like tokens.",
    )
    stdin: str | None = Field(
        default=None,
        description="Optional stdin for commands like tee. When chain='pipe', stdin is set automatically from the previous command's output; manual stdin is for piping content into tools like tee. Framed pipe traces use placeholders for piped stdin/stdout on non-final steps to avoid echoing large intermediate output.",
    )
    model_config = ConfigDict(extra="forbid")


class RunFileCommands(Empty):
    """Input model for the ``run_file_command`` tool.

    Pass one or more commands in file_commands and always provide chain explicitly:
    pipe (stdout->stdin) or and (sequential, no passthrough).
    """

    chain: Literal["pipe", "and"] = Field(
        ...,
        description="Required chain type for execution: 'pipe' (stdout->stdin) or 'and' (sequential, no passthrough)",
    )
    file_commands: list[RunFileCommand] = Field(
        ..., min_length=1, description="List of commands to execute sequentially"
    )
    accumulated_output: SkipJsonSchema[str] = Field(
        default="",
        description="(Internal) Carries framed output across AND and pipe chain iterations.",
    )
    model_config = ConfigDict(extra="forbid")


__all__ = ["RunFileCommand", "RunFileCommands"]
