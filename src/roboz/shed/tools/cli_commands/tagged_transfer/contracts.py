"""Input and preparation contracts for the experimental tagged CLI."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import Literal

from pydantic import ConfigDict, Field

from roboz.models import Empty
from roboz.shed.models import CommandReady, Operation

type TokenTag = Literal["CMD", "FLG", "ARG", "PTH"]
type TaggedToken = tuple[str, TokenTag]


class TaggedFileCommand(Empty):
    """One command expressed as ordered [value, tag] pairs."""

    value: list[TaggedToken] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered [value, tag] pairs. Start with ['cp', 'CMD'] or ['mv', 'CMD']; "
            "tag flags FLG and literal paths PTH. ARG is unsupported for these commands."
        ),
    )
    model_config = ConfigDict(extra="forbid")


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

    command: TaggedToken
    allowed: tuple[TokenRule, ...]
    forbidden_pairs: tuple[tuple[str, str], ...]
    prepare_command: Callable[[ParsedCommand, Path], PreparedCommand]

    @property
    def name(self) -> str:
        """Return the executable name declared by the command token."""
        return self.command[0]
