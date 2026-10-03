"""Transfer mapping and collision checks shared by cp and mv."""

import os
import re
import shlex
import stat
from itertools import combinations, product
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TokenRule
from ..paths import expand_source_path, resolve_literal_path
from ..tokens import END_OPTIONS, PATH_TOKEN


def _validate_transfer_source(source: Path) -> None:
    """Accept directories and unaliased regular files without following links."""
    if source.is_symlink():
        raise ValueError(f"Symlinks are unsupported: {source}")
    if source.is_dir():
        return
    if not source.is_file():
        raise ValueError(
            f"Source must be an existing regular file or directory: {source}"
        )
    if source.stat().st_nlink > 1:
        raise ValueError(f"Hard-linked files are unsupported: {source}")


def _validate_transfer_target(
    target: Path, source_ids: set[tuple[int, int]], *, directory: bool
) -> None:
    """Require a usable destination that does not alias any source entry."""
    if target.is_symlink():
        raise ValueError(f"Symlinks are unsupported: {target}")
    if not target.exists():
        return
    if directory and not target.is_dir():
        raise ValueError(f"Destination must be a directory: {target}")
    if not directory and not target.is_file():
        raise ValueError(f"Destination must be a regular file: {target}")
    target_stat = target.stat()
    if not directory and target_stat.st_nlink > 1:
        raise ValueError(f"Hard-linked files are unsupported: {target}")
    if (target_stat.st_dev, target_stat.st_ino) in source_ids:
        raise ValueError(f"Destination is also a source: {target}")


def _validate_transfer_overlap(transfers: list[tuple[Path, Path]]) -> None:
    """Allow nested sources while rejecting duplicate sources and output conflicts."""
    sources = [source for source, _ in transfers]
    targets = [target for _, target in transfers]
    seen: set[Path] = set()
    for source in sources:
        if source in seen:
            raise ValueError(
                f"Duplicate source: {source}. Remove repeated operands or narrow "
                "the source patterns."
            )
        seen.add(source)
    for pairs, explanation in (
        (combinations(targets, 2), "Conflicting destinations; use distinct output paths"),
        (product(sources, targets), "Source/destination overlap; choose separate paths"),
    ):
        for left, right in pairs:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError(f"{explanation}: {left}, {right}")


def resolve_transfers(
    sources: list[Path],
    destination: Path,
    *,
    into_directory: bool,
    source_names: list[str],
) -> list[tuple[Path, Path]]:
    """Validate transfer roots, retaining GNU's contents-copy operand syntax."""
    source_ids: set[tuple[int, int]] = set()
    for source in sources:
        _validate_transfer_source(source)
        source_stat = source.stat()
        source_ids.add((source_stat.st_dev, source_stat.st_ino))
    transfers: list[tuple[Path, Path]] = []
    for source, name in zip(sources, source_names, strict=True):
        target = destination
        if into_directory and name not in {".", ".."}:
            target /= name
        if not target.parent.is_dir():
            raise ValueError(f"Destination parent must exist: {target.parent}")
        _validate_transfer_target(target, source_ids, directory=source.is_dir())
        transfers.append((source, target))
    _validate_transfer_overlap(transfers)
    return transfers


def expand_transfers(transfers: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Map every tree entry, caching source scans across overlapping operands."""
    expanded: list[tuple[Path, Path]] = []
    source_ids: set[tuple[int, int]] = set()
    source_entries: dict[Path, tuple[bool, list[Path]]] = {}
    pending = list(reversed(transfers))
    while pending:
        source, target = pending.pop()
        if source not in source_entries:
            _validate_transfer_source(source)
            source_stat = source.stat()
            source_ids.add((source_stat.st_dev, source_stat.st_ino))
            is_directory = stat.S_ISDIR(source_stat.st_mode)
            children = sorted(source.iterdir(), reverse=True) if is_directory else []
            source_entries[source] = is_directory, children
        _, children = source_entries[source]
        expanded.append((source, target))
        pending.extend((child, target / child.name) for child in children)
    # Parents precede children: a missing parent is a directory planned above.
    for source, target in expanded:
        is_directory, _ = source_entries[source]
        _validate_transfer_target(target, source_ids, directory=is_directory)
    return expanded


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
    END_OPTIONS,
    PATH_TOKEN,
)
