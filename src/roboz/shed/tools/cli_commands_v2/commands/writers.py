"""Path checks and permission requirements shared by tee and touch."""

import os
import stat
from pathlib import Path

from roboz.shed.models import Operation

from ..paths import resolve_literal_path


def inspect_file_path(spelling: str, base: Path) -> tuple[Path, os.stat_result | None]:
    """Inspect a regular file or directory, rejecting links and special files."""
    path = resolve_literal_path(spelling, base)
    try:
        entry = os.stat(spelling)
    except FileNotFoundError:
        return path, None
    if not (stat.S_ISREG(entry.st_mode) or stat.S_ISDIR(entry.st_mode)):
        raise ValueError(f"Path must be a regular file or directory: {path}")
    if stat.S_ISREG(entry.st_mode) and entry.st_nlink > 1:
        raise ValueError(f"Hard-linked files are unsupported: {path}")
    return path, entry


def write_operations(
    spelling: str,
    base: Path,
    *,
    allow_directory: bool = False,
    no_create: bool = False,
) -> list[tuple[Operation, Path]]:
    """Require CREATE and existing-file READ/DELETE before any native writes."""
    path, entry = inspect_file_path(spelling, base)
    if entry is None and not no_create:
        if spelling.endswith("/") or os.path.basename(spelling) in {".", ".."}:
            raise ValueError(f"Directory must already exist: {spelling}")
        # Check the executable spelling, so missing/../file cannot be normalized
        # into an otherwise valid creation target.
        parent = Path(os.path.dirname(spelling))
        if not parent.is_dir():
            raise ValueError(f"Target parent must exist: {parent}")
    operations = [(Operation.CREATE, path)]
    if entry is not None:
        if stat.S_ISDIR(entry.st_mode):
            if not allow_directory:
                raise ValueError(f"Target must be a regular file: {path}")
        else:
            operations.extend(((Operation.READ, path), (Operation.DELETE, path)))
    return operations
