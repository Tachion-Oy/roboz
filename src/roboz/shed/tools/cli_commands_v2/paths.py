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
    resolved_path = path.resolve()
    if value.endswith("/") and resolved_path.exists() and not resolved_path.is_dir():
        raise ValueError(f"A trailing slash requires a directory: {value!r}")
    return resolved_path


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


def _expand_globstar(prefix: str, base: Path, *, include_root: bool) -> list[str]:
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
                matches.append(child_prefix)
    return matches


def _expand_globstar_matches(
    prefixes: list[str], base: Path, *, include_root: bool
) -> list[str]:
    """Collect recursive matches beneath each source prefix."""
    return [
        match
        for prefix in prefixes
        for match in _expand_globstar(prefix, base, include_root=include_root)
    ]


def expand_source_path(
    value: str, base: Path, *, preserve_relative: bool = False
) -> list[str]:
    """Expand stars and one recursive **/ component using zsh's default behavior.

    A final ** without a slash behaves like * and selects immediate children.
    Hidden names require a leading dot in their pattern component. Missing or
    non-directory branches do not match; symlinks and traversal errors reject
    preparation. Results use filesystem byte order and are never re-expanded.
    By default results are absolute. preserve_relative retains relative operand
    text, including ./ and trailing slashes, for native output and matching.
    """
    path_arg = os.path.join(str(base), value)
    if "*" not in value:
        return [value if preserve_relative else path_arg]
    parts = value.split("/")
    if "***" in parts[:-1]:
        raise ValueError("Symlink-following '***/' patterns are unsupported")
    if parts[:-1].count("**") > 1:
        raise ValueError(
            f"Only one recursive '**' component is supported per source: {value!r}. "
            "Use a pattern such as 'src/**/*.py'."
        )
    first_pattern = next(index for index, part in enumerate(parts) if "*" in part)
    literal_prefix = "/".join(parts[:first_pattern]) + "/" if first_pattern else ""
    prefixes = [os.path.join(str(base), literal_prefix)]
    for index, part in enumerate(parts[first_pattern:], start=first_pattern):
        if part == "**" and index < len(parts) - 1:
            prefixes = _expand_globstar_matches(
                prefixes,
                base,
                # Bare **/ omits the implicit current directory.
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
    if preserve_relative and not os.path.isabs(value):
        prefix = os.path.join(str(base), "")
        results = [path.removeprefix(prefix) for path in results]
    return sorted(results, key=os.fsencode)
