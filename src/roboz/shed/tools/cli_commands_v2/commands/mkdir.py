"""Tagged directory creation with explicit target and missing-parent permissions."""

import os
import re
import shlex
import stat
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import END_OPTIONS, PATH_TOKEN


def _directory_operations(
    path_arg: str, *, parents: bool
) -> list[tuple[Operation, Path]]:
    """Inspect components before collapsing '..', retaining every possible creation.

    Inspect canonical prefixes so a missing directory before '..' cannot hide a
    later symlink or non-directory. Missing parents without -p and existing
    directory errors are left to native mkdir, preserving ordered partial effects.
    """
    path = Path(path_arg)
    current = Path(path.anchor).resolve()
    operations: list[tuple[Operation, Path]] = []
    for part in path.parts[1:]:
        current = current.parent if part == ".." else current / part
        try:
            entry = current.lstat()
        except FileNotFoundError:
            if parents:
                operations.append((Operation.CREATE, current))
            continue
        if stat.S_ISLNK(entry.st_mode):
            raise ValueError(f"Symlinks are unsupported: {current}")
        if not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f"Path must be a directory: {current}")
    operations.append((Operation.CREATE, current))
    return operations


def prepare_mkdir(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require CREATE on each literal target and every missing -p parent."""
    if not parsed.operands:
        raise ValueError("mkdir requires at least one target PTH")
    argv = parsed.argv[: parsed.operands[0]]
    operations: list[tuple[Operation, Path]] = []
    for index in parsed.operands:
        value = parsed.tokens[index][0]
        if "*" in value:
            raise ValueError("mkdir target PTH must be literal; '*' is unsupported")
        path_arg = os.path.join(str(base), value)
        operations.extend(
            _directory_operations(path_arg, parents="-p" in parsed.options)
        )
        argv.append(path_arg)
    return PreparedCommand(
        ready=CommandReady(
            command_name="mkdir",
            argv=argv,
            base_workdir=base,
            stdin="",
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


MKDIR = TaggedCommandSpec(
    command=("mkdir", "CMD"),
    allowed=(
        END_OPTIONS,
        PATH_TOKEN,
        TokenRule(tag="FLG", pattern=re.compile(r"-p|--parents"), option="-p"),
        TokenRule(tag="FLG", pattern=re.compile(r"-v|--verbose"), option="-v"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_mkdir,
)
