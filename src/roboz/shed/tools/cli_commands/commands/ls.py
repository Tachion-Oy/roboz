"""Native directory listings with permissions on explicit operands."""

import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec, TokenRule
from ..paths import expand_source_path
from ..tokens import PATH_TOKEN
from .writers import inspect_file_path


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
        for path_arg in expand_source_path(value, base, preserve_relative=True):
            resolved_path, _ = inspect_file_path(path_arg, base)
            argv.append("./" + path_arg if path_arg.startswith("-") else path_arg)
            operations.append((Operation.READ, resolved_path))
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


LS = CommandSpec(
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
