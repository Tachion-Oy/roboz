"""Explicit cp/mv token policies and command-specific filesystem effects."""

import os
import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from .contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from .helpers import expand_transfers, resolve_literal_path, resolve_transfers


def _transfer_arguments(
    parsed: ParsedCommand, base: Path
) -> tuple[list[str], list[tuple[Path, Path]]]:
    """Interpret the destination modes shared by the supported cp/mv forms."""
    if "-t" in parsed.options:
        destination_index = parsed.options["-t"] + 1
        source_indices = parsed.operands
    else:
        if len(parsed.operands) < 2:
            raise ValueError("Provide at least one source PTH and a destination PTH")
        *source_indices, destination_index = parsed.operands
    if not source_indices:
        raise ValueError("Provide at least one source PTH")
    if "-T" in parsed.options and len(source_indices) != 1:
        raise ValueError("-T requires exactly one source")

    argv = list(parsed.argv)
    for index in [*source_indices, destination_index]:
        # Path normalization would erase meaningful suffixes such as 'src/.'.
        argv[index] = os.path.join(str(base), argv[index])
    sources = [resolve_literal_path(argv[index], base) for index in source_indices]
    destination = resolve_literal_path(argv[destination_index], base)
    if "-t" in parsed.options and not destination.is_dir():
        raise ValueError("-t requires an existing destination directory")
    if len(sources) > 1 and not destination.is_dir():
        raise ValueError("Multiple sources require an existing destination directory")
    transfers = resolve_transfers(
        sources,
        destination,
        into_directory=destination.is_dir() and "-T" not in parsed.options,
        source_names=[
            os.path.basename(argv[index].rstrip("/")) for index in source_indices
        ],
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
    TokenRule(tag="PTH", pattern=re.compile(r"[^*?\[\]\x00]+")),
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
COMMANDS = {spec.name: spec for spec in (CP, MV)}
