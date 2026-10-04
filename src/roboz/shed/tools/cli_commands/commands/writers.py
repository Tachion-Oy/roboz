"""Path checks and permission requirements shared by tee and touch."""

import os
import stat
from pathlib import Path

from roboz.shed.models import Operation

from ..paths import resolve_literal_path


def inspect_file_path(path_arg: str, base: Path) -> tuple[Path, os.stat_result | None]:
    """Inspect a file or directory relative to base, rejecting links and special files."""
    resolved_path = resolve_literal_path(path_arg, base)
    try:
        entry = os.stat(os.path.join(str(base), path_arg))
    except FileNotFoundError:
        return resolved_path, None
    if not (stat.S_ISREG(entry.st_mode) or stat.S_ISDIR(entry.st_mode)):
        raise ValueError(f"Path must be a regular file or directory: {resolved_path}")
    if stat.S_ISREG(entry.st_mode) and entry.st_nlink > 1:
        raise ValueError(f"Hard-linked files are unsupported: {resolved_path}")
    return resolved_path, entry


def write_operations(
    path_arg: str,
    base: Path,
    *,
    allow_directory: bool = False,
    no_create: bool = False,
    create_parents: bool = False,
) -> list[tuple[Operation, Path]]:
    """Require CREATE and existing-file READ/DELETE before writing.

    The patch tool may create missing parents; CLI writes require existing ones.
    """
    resolved_path, entry = inspect_file_path(path_arg, base)
    if entry is None and not no_create:
        if path_arg.endswith("/") or os.path.basename(path_arg) in {".", ".."}:
            raise ValueError(f"Directory must already exist: {path_arg}")
        # Check the path argument, so missing/../file cannot be normalized
        # into an otherwise valid creation target.
        parent = base / os.path.dirname(path_arg)
        if not create_parents and not parent.is_dir():
            raise ValueError(f"Target parent must exist: {parent}")
    operations = [(Operation.CREATE, resolved_path)]
    if entry is not None:
        if stat.S_ISDIR(entry.st_mode):
            if not allow_directory:
                raise ValueError(f"Target must be a regular file: {resolved_path}")
        else:
            operations.extend(
                ((Operation.READ, resolved_path), (Operation.DELETE, resolved_path))
            )
    return operations
