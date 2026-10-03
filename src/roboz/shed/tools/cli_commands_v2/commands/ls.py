"""Native directory listings with permissions on explicit operands."""

import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import PATH_TOKEN
from .discovery import discovery_paths


def prepare_ls(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Keep native option order, combinations, and output after checking operands."""
    argv = ["ls"]
    operations: list[tuple[Operation, Path]] = []
    options_ended = False
    for value, tag in parsed.tokens[1:]:
        if tag == "FLG":
            if options_ended:
                raise ValueError("After --, ls accepts only PTH operands")
            options_ended = value == "--"
            argv.append(value)
            continue
        for spelling, path in discovery_paths(value, base):
            argv.append("./" + spelling if spelling.startswith("-") else spelling)
            operations.append((Operation.READ, path))
    if not operations:
        operations.append((Operation.READ, base))
    return PreparedCommand(
        ready=CommandReady(
            command_name="ls",
            argv=argv,
            base_workdir=base,
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


LS = TaggedCommandSpec(
    command=("ls", "CMD"),
    # Command-local preparation keeps native interspersed/repeated/bundled flags.
    allowed=(
        PATH_TOKEN,
        TokenRule(
            tag="FLG",
            pattern=re.compile(
                r"-[laAhdR1rtSUFpisn]+|--(?:all|almost-all|human-readable|directory|"
                r"recursive|reverse|classify|inode|size|numeric-uid-gid)|--"
            ),
        ),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_ls,
)
