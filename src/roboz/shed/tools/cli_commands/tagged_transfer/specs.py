"""Explicit tagged command token policies and command-specific filesystem effects."""

import os
import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from .contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from .helpers import (
    expand_source_path,
    expand_transfers,
    prepare_counted_read,
    prepare_read,
    resolve_literal_path,
    resolve_transfers,
)


def _transfer_arguments(
    parsed: ParsedCommand, base: Path
) -> tuple[list[str], list[tuple[Path, Path]]]:
    """Interpret the destination modes shared by the supported cp/mv forms."""
    arguments = parsed.argv
    if "-t" in parsed.options:
        destination_index = parsed.options["-t"] + 1
        source_indices = parsed.operands
    else:
        if len(parsed.operands) < 2:
            raise ValueError("Provide at least one source PTH and a destination PTH")
        *source_indices, destination_index = parsed.operands
    if not source_indices:
        raise ValueError("Provide at least one source PTH")
    destination_value = arguments[destination_index]
    if "*" in destination_value:
        raise ValueError("Destination PTH must be literal; '*' is unsupported")
    source_arguments = {
        index: expand_source_path(arguments[index], base) for index in source_indices
    }
    source_values = [value for values in source_arguments.values() for value in values]
    if "-T" in parsed.options and len(source_values) != 1:
        raise ValueError("-T requires exactly one source")

    # Path normalization would erase meaningful suffixes such as 'src/.'.
    destination_value = os.path.join(str(base), destination_value)
    argv: list[str] = []
    for index, value in enumerate(arguments):
        if index in source_arguments:
            argv.extend(source_arguments[index])
        else:
            argv.append(destination_value if index == destination_index else value)
    sources = [resolve_literal_path(value, base) for value in source_values]
    destination = resolve_literal_path(destination_value, base)
    if "-t" in parsed.options and not destination.is_dir():
        raise ValueError("-t requires an existing destination directory")
    if len(sources) > 1 and not destination.is_dir():
        raise ValueError("Multiple sources require an existing destination directory")
    transfers = resolve_transfers(
        sources,
        destination,
        into_directory=destination.is_dir() and "-T" not in parsed.options,
        source_names=[os.path.basename(value.rstrip("/")) for value in source_values],
    )
    return argv, transfers


def _prepared_transfer(
    argv: list[str],
    transfers: list[tuple[Path, Path]],
    base: Path,
    source_operation: Operation,
) -> PreparedCommand:
    operations: list[tuple[Operation, Path]] = []
    for source, destination in transfers:
        operations.append((source_operation, source))
        operations.append((Operation.CREATE, destination))
        if destination.exists() and (
            not destination.is_dir() or source_operation == Operation.DELETE
        ):
            operations.append((Operation.READ, destination))
            operations.append((Operation.DELETE, destination))
    return PreparedCommand(
        ready=CommandReady(
            command_name=argv[0],
            argv=argv,
            base_workdir=base,
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


def prepare_cp(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require source READ and effective destination CREATE for files and trees."""
    argv, transfers = _transfer_arguments(parsed, base)
    if "-R" not in parsed.options and any(source.is_dir() for source, _ in transfers):
        raise ValueError("Directory copies require -r, -R, or --recursive")
    return _prepared_transfer(argv, expand_transfers(transfers), base, Operation.READ)


def prepare_mv(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require DELETE/CREATE throughout a tree for same-filesystem moves."""
    argv, transfers = _transfer_arguments(parsed, base)
    for source, destination in transfers:
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise ValueError("Cross-filesystem moves are unsupported")
        if destination.is_dir() and any(destination.iterdir()):
            raise ValueError(f"Move destination directory must be empty: {destination}")
    return _prepared_transfer(argv, expand_transfers(transfers), base, Operation.DELETE)


def prepare_pwd(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Force physical output independently of PWD and POSIXLY_CORRECT."""
    argv = parsed.argv
    if "-P" not in parsed.options:
        argv.insert(1, "-P")
    return PreparedCommand(
        ready=CommandReady(
            command_name="pwd",
            argv=argv,
            base_workdir=base,
            display_command=shlex.join(argv),
        ),
        operations=[(Operation.READ, base)],
    )


_TRANSFER_TOKENS = (
    TokenRule(
        tag="FLG",
        pattern=re.compile(r"-t|--target-directory"),
        option="-t",
        takes="PTH",
    ),
    TokenRule(tag="FLG", pattern=re.compile(r"-T|--no-target-directory"), option="-T"),
    TokenRule(tag="FLG", pattern=re.compile(r"-v|--verbose"), option="-v"),
    TokenRule(tag="FLG", pattern=re.compile(r"-f|--force"), option="-f"),
    TokenRule(
        tag="FLG",
        pattern=re.compile(r"--strip-trailing-slashes"),
        option="--strip-trailing-slashes",
    ),
    TokenRule(tag="FLG", pattern=re.compile(r"--"), option="--", ends_options=True),
    TokenRule(tag="PTH", pattern=re.compile(r"[^?\[\]\x00]+", re.DOTALL)),
)

CP = TaggedCommandSpec(
    command=("cp", "CMD"),
    allowed=(
        *_TRANSFER_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-r|-R|--recursive"), option="-R"),
    ),
    forbidden_pairs=(("-t", "-T"),),
    prepare_command=prepare_cp,
)
MV = TaggedCommandSpec(
    command=("mv", "CMD"),
    allowed=_TRANSFER_TOKENS,
    forbidden_pairs=(("-t", "-T"),),
    prepare_command=prepare_mv,
)

_READ_TOKENS = (
    TokenRule(tag="FLG", pattern=re.compile(r"--"), option="--", ends_options=True),
    TokenRule(tag="PTH", pattern=re.compile(r"[^?\[\]\x00]+", re.DOTALL)),
    TokenRule(tag="ARG", pattern=re.compile(r"-")),
)

PWD = TaggedCommandSpec(
    command=("pwd", "CMD"),
    allowed=(
        TokenRule(tag="FLG", pattern=re.compile(r"--"), option="--", ends_options=True),
        TokenRule(tag="FLG", pattern=re.compile(r"-P|--physical"), option="-P"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_pwd,
)

CAT = TaggedCommandSpec(
    command=("cat", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-n|--number"), option="-n"),
        TokenRule(tag="FLG", pattern=re.compile(r"-b|--number-nonblank"), option="-b"),
        TokenRule(tag="FLG", pattern=re.compile(r"-s|--squeeze-blank"), option="-s"),
        TokenRule(tag="FLG", pattern=re.compile(r"-E|--show-ends"), option="-E"),
        TokenRule(tag="FLG", pattern=re.compile(r"-T|--show-tabs"), option="-T"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_read,
)

HEAD = TaggedCommandSpec(
    command=("head", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="ARG", pattern=re.compile(r"[0-9]+")),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-n|--lines"), option="-n", takes="ARG"
        ),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-c|--bytes"), option="-c", takes="ARG"
        ),
        TokenRule(tag="FLG", pattern=re.compile(r"-q|--quiet|--silent"), option="-q"),
        TokenRule(tag="FLG", pattern=re.compile(r"-v|--verbose"), option="-v"),
    ),
    forbidden_pairs=(("-n", "-c"), ("-q", "-v")),
    prepare_command=prepare_counted_read,
)

TAIL = TaggedCommandSpec(
    command=("tail", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="ARG", pattern=re.compile(r"[0-9]+")),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-n|--lines"), option="-n", takes="ARG"
        ),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-c|--bytes"), option="-c", takes="ARG"
        ),
        TokenRule(tag="FLG", pattern=re.compile(r"-q|--quiet|--silent"), option="-q"),
        TokenRule(tag="FLG", pattern=re.compile(r"-v|--verbose"), option="-v"),
    ),
    forbidden_pairs=(("-n", "-c"), ("-q", "-v")),
    prepare_command=prepare_counted_read,
)

WC = TaggedCommandSpec(
    command=("wc", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-l|--lines"), option="-l"),
        TokenRule(tag="FLG", pattern=re.compile(r"-w|--words"), option="-w"),
        TokenRule(tag="FLG", pattern=re.compile(r"-c|--bytes"), option="-c"),
        TokenRule(tag="FLG", pattern=re.compile(r"-m|--chars"), option="-m"),
        TokenRule(tag="FLG", pattern=re.compile(r"-L|--max-line-length"), option="-L"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_read,
)

COMMANDS = {spec.name: spec for spec in (CP, MV, PWD, CAT, HEAD, TAIL, WC)}
