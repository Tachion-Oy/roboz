"""Input and preparation contracts for the experimental tagged CLI."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from roboz.models import Empty
from roboz.shed.models import CommandReady, Operation

type TokenTag = Literal["CMD", "FLG", "ARG", "PTH", "CTL"]
type CommandName = Literal["cp", "mv"]
type ControlOperator = Literal["&&", "||", ";", "|"]
type TaggedToken = (
    tuple[CommandName, Literal["CMD"]]
    | tuple[ControlOperator, Literal["CTL"]]
    | tuple[str, Literal["FLG", "ARG", "PTH"]]
)


class TaggedFileCommand(Empty):
    """A command sequence expressed as one stream of ordered [value, tag] pairs."""

    value: list[TaggedToken] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered [value, tag] pairs. Each command starts with ['cp', 'CMD'] or "
            "['mv', 'CMD']; separate commands with ['&&', 'CTL'], ['||', 'CTL'], "
            "[';', 'CTL'], or ['|', 'CTL'] using Bash control flow. "
            "Tag flags FLG and paths PTH. Sources allow '*' within components and "
            "one standalone recursive '**' component, e.g. 'src/**/*.py'; "
            "destinations must be literal. '?' and bracket patterns are "
            "unsupported. ARG is unsupported for these commands."
        ),
    )
    model_config = ConfigDict(extra="forbid")

    @field_validator("value")
    @classmethod
    def validate_sequence(cls, tokens: list[TaggedToken]) -> list[TaggedToken]:
        """Validate control syntax; command options and paths are checked when reached."""
        expect_command = True
        for _, tag in tokens:
            if tag == "CTL":
                if expect_command:
                    raise ValueError("A CTL operator must follow a command")
                expect_command = True
            elif expect_command:
                if tag != "CMD":
                    raise ValueError("Each command must start with a CMD token")
                expect_command = False
            elif tag == "CMD":
                raise ValueError("Separate commands with a CTL operator")
        if expect_command:
            raise ValueError("A CTL operator must be followed by a command")
        return tokens


class CommandExecution(Empty):
    """Execution data carried through the existing resolve/guard/result flow."""

    request: TaggedFileCommand
    remaining: list[TaggedToken]
    stdin: str | None = None
    accumulated_output: str = ""
    failure: str | None = None


@dataclass(frozen=True)
class TokenRule:
    """Allow a tagged token, optionally consuming the next token as a value."""

    tag: TokenTag
    pattern: Pattern[str]
    option: str | None = None
    takes: TokenTag | None = None
    ends_options: bool = False


@dataclass(frozen=True)
class ParsedCommand:
    """Validated argv with positional indices and canonical option indices."""

    argv: list[str]
    operands: list[int]
    options: dict[str, int]


@dataclass(frozen=True)
class PreparedCommand:
    """Executable payload and all filesystem permissions it requires."""

    ready: CommandReady
    operations: list[tuple[Operation, Path]]


@dataclass(frozen=True)
class TaggedCommandSpec:
    """Supported tokens, option conflicts, and command-specific preparation."""

    command: tuple[CommandName, Literal["CMD"]]
    allowed: tuple[TokenRule, ...]
    forbidden_pairs: tuple[tuple[str, str], ...]
    prepare_command: Callable[[ParsedCommand, Path], PreparedCommand]

    @property
    def name(self) -> str:
        """Return the executable name declared by the command token."""
        return self.command[0]
