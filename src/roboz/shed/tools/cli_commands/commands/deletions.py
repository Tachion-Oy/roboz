"""No-follow entry inspection and DELETE requirements shared by rm and gio trash."""

import shlex
import stat
from collections.abc import Callable
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import PreparedCommand
from ..paths import (
    directory_entries,
    expand_source_path,
    inspect_entry_path,
    preparation_deadline,
)


def _delete_operations(
    paths: list[str],
    base: Path,
    check_deadline: Callable[[], None],
    *,
    recursive: bool,
) -> list[tuple[Operation, Path]]:
    """Collect DELETE requirements in operand and traversal order without following links."""
    operations: list[tuple[Operation, Path]] = []
    seen: set[Path] = set()
    pending = list(reversed(paths))
    while pending:
        check_deadline()
        current = pending.pop()
        location, entry = inspect_entry_path(current, base)
        # Inspect repeated arguments too: suffixes can change whether the native
        # command reaches an entry. Missing branches must not mark a tree seen.
        if location in seen:
            continue
        operations.append((Operation.DELETE, location))
        if entry is None:
            continue
        seen.add(location)
        if not recursive or not stat.S_ISDIR(entry.st_mode):
            continue
        children = [
            entry.path for entry in directory_entries(Path(current), check_deadline)
        ]
        pending.extend(sorted(children, reverse=True))
    check_deadline()
    return list(dict.fromkeys(operations))


def prepare_deletion(
    argv: list[str], operands: list[str], base: Path, *, recursive: bool
) -> PreparedCommand:
    """Guard all selected entries before one native invocation, retaining argv text."""
    check_deadline = preparation_deadline("Deletion")
    paths: list[str] = []
    for operand in operands:
        matches = expand_source_path(
            operand, base, allow_terminal_symlinks=True, check_deadline=check_deadline
        )
        paths.extend(matches)
    operations = _delete_operations(paths, base, check_deadline, recursive=recursive)
    argv = [*argv, *paths]
    return PreparedCommand(
        ready=CommandReady(
            command_name=argv[0],
            argv=argv,
            base_workdir=base,
            stdin="",
            display_command=shlex.join(argv),
        ),
        operations=operations,
    )
