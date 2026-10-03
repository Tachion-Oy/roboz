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
type CommandName = Literal[
    "cp", "mv", "pwd", "cat", "head", "tail", "wc", "tee", "touch", "mkdir", "grep", "rg", "ls", "find"
]
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
            "Ordered [value, tag] pairs. Start each command with cp, mv, pwd, cat, "
            "head, tail, wc, tee, touch, mkdir, grep, rg, ls, or find tagged CMD; separate commands with "
            "['&&', 'CTL'], ['||', 'CTL'], "
            "[';', 'CTL'], or ['|', 'CTL'] using Bash control flow. "
            "Tag flags FLG and paths PTH. Sources allow '*' within components and "
            "one recursive '**/' component, e.g. 'src/**/*.py'; final '**' acts like '*'. "
            "transfer/tee destinations, mkdir targets, and touch references must be literal. "
            "touch targets also allow patterns; unmatched patterns fail before execution. "
            "'?' and bracket patterns are "
            "unsupported in PTH. head/tail counts use unsigned decimal ARG values; "
            "reader stdin uses ['-', 'ARG']. A '-' tagged PTH names a literal "
            "file. Omitted reader paths use stdin. tee accepts one inline content ARG "
            "after flags and before destination PTHs; piped input overrides it. "
            "touch -d/-t values use ARG; -r uses PTH. mkdir accepts -p/--parents, "
            "-v/--verbose, and --; CREATE is required on each target and parent created by -p. "
            "grep/rg take one pattern ARG before input PTHs or stdin '-' ARG; "
            "their count/context options take unsigned decimal ARG values. "
            "ls accepts bundled flags such as -lah. find takes roots tagged PTH before "
            "predicates/operators tagged FLG and their values tagged ARG; predicate patterns "
            "are passed literally. ls/find require READ on roots only, defaulting to base."
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
    """Execution progress with pipe bytes separate from the ready command's text stdin."""

    request: TaggedFileCommand
    remaining: list[TaggedToken]
    stdin: bytes | None = None
    accumulated_output: str = ""
    failure: str | None = None
    ready: CommandReady | None = None

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


@dataclass(frozen=True)
class TokenRule:
    """Declare an accepted token and how it participates in argument validation.

    The validator selects the first rule in a command's ``allowed`` tuple whose
    tag and pattern both match. An unmatched token rejects the command before
    execution. Matching a rule validates syntax; command preparation determines
    the required filesystem operations, which the permission guard authorizes.

    Args:
        tag: Required token role, such as FLG for an option, PTH for a path,
            or ARG for a count or stdin marker. The value must carry this tag
            explicitly; its text alone does not determine its role.
        pattern: Regular expression applied with ``fullmatch`` to the token's
            entire value. For an option taking a value, this matches the option
            name itself, not the following value.
        option: Canonical key recorded in ``ParsedCommand.options`` with the
            option's token index. Aliases such as -n and --lines share one key
            so duplicate and conflicting options can be rejected. The original
            text is preserved in argv. None makes the token a positional
            operand; options must precede positional operands.
        takes: Required tag of the immediately following option value, or None
            if the option takes no value. The following token must also match
            an allowed rule for this command. It is consumed with the option
            and is not recorded as a positional operand. Used only when
            ``option`` is set; command preparation checks any further constraints
            on the value, such as requiring an unsigned count.
        ends_options: Mark this option as the option terminator, normally --.
            Later options are rejected, while positional operands still need
            to match allowed rules. Used only when ``option`` is set.

    For example, a rule with tag FLG, pattern ``-n|--lines``, option ``-n``,
    and takes ARG accepts either alias followed by an ARG token. The command
    must also declare which ARG values are allowed.
    """

    tag: TokenTag
    pattern: Pattern[str]
    option: str | None = None
    takes: TokenTag | None = None
    ends_options: bool = False


@dataclass(frozen=True)
class ParsedCommand:
    """Validated tokens with positional indices and canonical option indices."""

    tokens: list[TaggedToken]
    operands: list[int]
    options: dict[str, int]

    @property
    def argv(self) -> list[str]:
        """Return argument text without losing the original token roles."""
        return [value for value, _ in self.tokens]


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
