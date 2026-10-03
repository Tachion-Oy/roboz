"""Discovery operands with zsh star expansion and root-only permission checks."""

import os
from pathlib import Path

from ..paths import expand_source_path
from .writers import inspect_file_path


def discovery_paths(value: str, base: Path) -> list[tuple[str, Path]]:
    """Retain executable spelling separately from canonical permission locations.

    Only explicit/expanded operands are inspected; native commands discover
    children. Missing literal operands are left to native command error handling.
    """
    expanded = expand_source_path(value, base)
    paths: list[tuple[str, Path]] = []
    for spelling in expanded:
        path, _ = inspect_file_path(spelling, base)
        if not os.path.isabs(value):
            spelling = spelling.removeprefix(os.path.join(str(base), ""))
        paths.append((spelling, path))
    return paths
