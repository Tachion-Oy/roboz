"""Resolve literal paths and expand source patterns without following symlinks."""

import os
import stat
from collections.abc import Callable, Iterator
from fnmatch import fnmatchcase
from functools import partial
from os import scandir
from pathlib import Path
from time import monotonic

from roboz.shed.tools.cli_commands.utilities.constants import SUBPROCESS_TIMEOUT_SECONDS


def _reject_symlink_components(path: Path) -> None:
    """Check lexical components before normalization can erase a link before '..'."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlinks are unsupported: {current}")


def resolve_literal_path(value: str, base: Path) -> Path:
    """Resolve a literal path while rejecting symlinks in its components."""
    path = base / value
    _reject_symlink_components(path)

    resolved_path = path.resolve()
    if value.endswith("/") and resolved_path.exists() and not resolved_path.is_dir():
        raise ValueError(f"A trailing slash requires a directory: {value!r}")
    return resolved_path


def inspect_entry_path(value: str, base: Path) -> tuple[Path, os.stat_result | None]:
    """Inspect the named entry without following links, rejecting symlink parents.

    Permission locations are normalized only after checking raw components,
    including dots and trailing separators that would traverse a terminal link.
    Missing and non-directory branches are left for the native command to report.
    """
    path_arg = os.path.join(str(base), value)
    parts = path_arg.split("/")
    current = "/"
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        if index < len(parts) - 1 and os.path.islink(current):
            raise ValueError(f"Symlink parents are unsupported: {current}")
    resolved_path = Path(os.path.abspath(path_arg))
    try:
        return resolved_path, os.lstat(path_arg)
    except (FileNotFoundError, NotADirectoryError):
        return resolved_path, None


def _source_path_stat(
    value: str, base: Path, *, allow_terminal_symlinks: bool = False
) -> os.stat_result | None:
    """Inspect an absolute pattern candidate, returning None for missing branches.

    Callers supply absolute argument strings, preserving dots and separators.
    Stat the argument spelling rather than its normalized permission location.
    """
    if allow_terminal_symlinks:
        return inspect_entry_path(value, base)[1]
    try:
        resolve_literal_path(value, base)
        # Stat the lexical path: 'missing/..' must not become a match.
        return Path(value).stat()
    except (FileNotFoundError, NotADirectoryError):
        return None


def preparation_deadline(operation: str) -> Callable[[], None]:
    """Start one preparation time budget and return its expiration check."""
    deadline = monotonic() + SUBPROCESS_TIMEOUT_SECONDS
    return partial(check_preparation_deadline, deadline, operation)


def check_preparation_deadline(deadline: float, operation: str) -> None:
    """Fail preparation when its shared traversal and validation budget expires."""
    if monotonic() >= deadline:
        raise ValueError(
            f"{operation} preparation timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds"
        )


def directory_entries(
    directory: Path, check_deadline: Callable[[], None]
) -> Iterator[os.DirEntry[str]]:
    """Enumerate direct entries under a shared preparation budget.

    Callers must validate the directory's symlink policy before enumeration.
    """
    check_deadline()
    with scandir(directory) as entries:
        for entry in entries:
            check_deadline()
            yield entry
    check_deadline()


def _unlimited() -> None:
    """Leave existing callers' pattern expansion without a time limit."""


def _split_source_pattern(value: str) -> tuple[str, list[str]]:
    """Validate a star pattern and split its literal prefix from its components.

    Empty components and dots retain meaningful argument spelling. The prefix
    includes its trailing separator, or is empty when the first component matches.
    """
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
    return literal_prefix, parts[first_pattern:]


def _expand_source_component(
    prefix: str, component: str, base: Path, check_deadline: Callable[[], None]
) -> list[str]:
    """Match one component beneath a validated directory without following links."""
    check_deadline()
    directory_stat = _source_path_stat(prefix.rstrip("/") or "/", base)
    if directory_stat is None or not stat.S_ISDIR(directory_stat.st_mode):
        return []
    if "*" not in component:
        return [prefix + component]
    matches: list[str] = []
    for child in directory_entries(Path(prefix), check_deadline):
        if (not child.name.startswith(".") or component.startswith(".")) and fnmatchcase(
            child.name, component
        ):
            matches.append(prefix + child.name)
    return matches


def _recursive_directory_prefixes(
    prefix: str,
    base: Path,
    *,
    include_root: bool,
    allow_terminal_symlinks: bool,
    check_deadline: Callable[[], None],
) -> list[str]:
    """Collect visible directory prefixes, retaining their trailing separators."""
    root_stat = _source_path_stat(prefix.rstrip("/") or "/", base)
    if root_stat is None or not stat.S_ISDIR(root_stat.st_mode):
        return []
    matches = [prefix] if include_root else []
    pending = [prefix]
    while pending:
        for child in _expand_source_component(pending.pop(), "*", base, check_deadline):
            check_deadline()
            child_stat = _source_path_stat(
                child, base, allow_terminal_symlinks=allow_terminal_symlinks
            )
            if child_stat is None:
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                child_prefix = child + "/"
                pending.append(child_prefix)
                matches.append(child_prefix)
    return matches


def expand_source_path(
    value: str,
    base: Path,
    *,
    preserve_relative: bool = False,
    allow_terminal_symlinks: bool = False,
    check_deadline: Callable[[], None] = _unlimited,
) -> list[str]:
    """Expand stars and one recursive **/ component using zsh's default behavior.

    A final ** without a slash behaves like * and selects immediate children.
    Hidden names require a leading dot in their pattern component. Missing or
    non-directory branches do not match; symlinks and traversal errors reject
    preparation. Results use filesystem byte order and are never re-expanded.
    By default results are absolute. preserve_relative retains relative operand
    text, including ./ and trailing slashes, for native output and matching.
    Deletion can opt into terminal symlink matches, including dangling links;
    recursive expansion then skips links when selecting directories to traverse.
    """
    check_deadline()
    path_arg = os.path.join(str(base), value)
    if "*" not in value:
        return [value if preserve_relative else path_arg]

    literal_prefix, components = _split_source_pattern(value)
    prefixes = [os.path.join(str(base), literal_prefix)]
    for index, component in enumerate(components):
        has_suffix = index < len(components) - 1
        matches: list[str] = []
        if component == "**" and has_suffix:
            # Bare **/ omits the implicit current directory.
            include_root = (
                bool(literal_prefix) or index > 0 or any(components[index + 1 :])
            )
            for prefix in prefixes:
                matches.extend(
                    _recursive_directory_prefixes(
                        prefix,
                        base,
                        include_root=include_root,
                        allow_terminal_symlinks=allow_terminal_symlinks,
                        check_deadline=check_deadline,
                    )
                )
        else:
            for prefix in prefixes:
                matches.extend(
                    _expand_source_component(prefix, component, base, check_deadline)
                )
            if has_suffix:
                matches = [match + "/" for match in matches]
        prefixes = matches

    results: list[str] = []
    for path in prefixes:
        check_deadline()
        if _source_path_stat(
            path, base, allow_terminal_symlinks=allow_terminal_symlinks
        ) is not None:
            results.append(path)
    if not results:
        raise ValueError(f"Source pattern has no matches: {value!r}")
    if preserve_relative and not os.path.isabs(value):
        prefix = os.path.join(str(base), "")
        results = [path.removeprefix(prefix) for path in results]
    results.sort(key=os.fsencode)
    check_deadline()
    return results
