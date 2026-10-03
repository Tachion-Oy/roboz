"""Resolve literal paths and expand source patterns without following symlinks."""

import os
import stat
from fnmatch import fnmatchcase
from pathlib import Path


def resolve_literal_path(value: str, base: Path) -> Path:
    """Resolve a literal path while rejecting symlinks in its components."""
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    # Check the lexical path before resolving: a symlink followed by '..'
    # must not disappear during normalization.
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlinks are unsupported: {current}")
    resolved = path.resolve()
    if value.endswith("/") and resolved.exists() and not resolved.is_dir():
        raise ValueError(f"A trailing slash requires a directory: {value!r}")
    return resolved


def _source_path_stat(value: str, base: Path) -> os.stat_result | None:
    """Reject symlinks and stat the lexical path, returning None for missing branches."""
    try:
        resolve_literal_path(value, base)
        # Stat the lexical path: 'missing/..' must not become a match.
        return Path(value).stat()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _expand_source_component(prefix: str, component: str, base: Path) -> list[str]:
    """Match one component beneath a validated directory without following links."""
    directory_stat = _source_path_stat(prefix.rstrip("/") or "/", base)
    if directory_stat is None or not stat.S_ISDIR(directory_stat.st_mode):
        return []
    if "*" not in component:
        return [prefix + component]
    return [
        prefix + child.name
        for child in Path(prefix).iterdir()
        if (not child.name.startswith(".") or component.startswith("."))
        and fnmatchcase(child.name, component)
    ]


def _expand_globstar(
    prefix: str, base: Path, *, directory_only: bool, include_root: bool
) -> list[str]:
    """Expand one recursive component, retaining directory prefixes for suffixes."""
    root_stat = _source_path_stat(prefix.rstrip("/") or "/", base)
    if root_stat is None or not stat.S_ISDIR(root_stat.st_mode):
        return []
    matches = [prefix] if include_root else []
    pending = [prefix]
    while pending:
        for child in _expand_source_component(pending.pop(), "*", base):
            child_stat = _source_path_stat(child, base)
            if child_stat is None:
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                child_prefix = child + "/"
                pending.append(child_prefix)
                matches.append(child_prefix if directory_only else child)
            elif not directory_only:
                matches.append(child)
    return matches


def _expand_globstar_matches(
    prefixes: list[str], base: Path, *, directory_only: bool, include_root: bool
) -> list[str]:
    """Collect recursive matches beneath each source prefix."""
    return [
        match
        for prefix in prefixes
        for match in _expand_globstar(
            prefix, base, directory_only=directory_only, include_root=include_root
        )
    ]


def expand_source_path(value: str, base: Path) -> list[str]:
    """Expand stars and one recursive ** component, retaining literal path suffixes.

    Hidden names require a leading dot in their pattern component. Missing or
    non-directory branches do not match; symlinks and traversal errors reject
    preparation. Results use filesystem byte order and are never re-expanded.
    """
    spelling = os.path.join(str(base), value)
    if "*" not in value:
        return [spelling]
    parts = value.split("/")
    if parts.count("**") > 1:
        raise ValueError(
            f"Only one recursive '**' component is supported per source: {value!r}. "
            "Use a pattern such as 'src/**/*.py'."
        )
    first_pattern = next(index for index, part in enumerate(parts) if "*" in part)
    literal_prefix = "/".join(parts[:first_pattern]) + "/" if first_pattern else ""
    prefixes = [os.path.join(str(base), literal_prefix)]
    for index, part in enumerate(parts[first_pattern:], start=first_pattern):
        if part == "**":
            prefixes = _expand_globstar_matches(
                prefixes,
                base,
                directory_only=index < len(parts) - 1,
                # Bare ** and **/ omit the implicit current directory.
                include_root=index > 0 or any(parts[index + 1 :]),
            )
            continue
        matches = [
            match
            for prefix in prefixes
            for match in _expand_source_component(prefix, part, base)
        ]
        prefixes = (
            [match + "/" for match in matches] if index < len(parts) - 1 else matches
        )
    results = [path for path in prefixes if _source_path_stat(path, base) is not None]
    if not results:
        raise ValueError(f"Source pattern has no matches: {value!r}")
    return sorted(results, key=os.fsencode)
