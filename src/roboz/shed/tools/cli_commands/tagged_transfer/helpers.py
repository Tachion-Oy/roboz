"""Small validation and filesystem preparation helpers for tagged commands."""

import os
import re
import shlex
import stat
from fnmatch import fnmatchcase
from itertools import combinations, product
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from .contracts import (
    ParsedCommand,
    PreparedCommand,
    TaggedCommandSpec,
    TaggedToken,
    TokenRule,
)


def _matching_rule(token: TaggedToken, spec: TaggedCommandSpec) -> TokenRule:
    value, tag = token
    for rule in spec.allowed:
        if tag == rule.tag and rule.pattern.fullmatch(value):
            return rule
    raise ValueError(f"Unsupported {tag} token for {spec.name}: {value!r}")


def _parse_arguments(
    tokens: list[TaggedToken], spec: TaggedCommandSpec
) -> ParsedCommand:
    """Dispatch argument tokens to operands or options, consuming option values."""
    operands: list[int] = []
    options: dict[str, int] = {}
    options_ended = False
    arguments = iter(enumerate(tokens[1:], start=1))
    for index, token in arguments:
        rule = _matching_rule(token, spec)
        if rule.option is None:
            operands.append(index)
            continue
        if options_ended:
            raise ValueError("Options are not allowed after the option terminator")
        if operands:
            raise ValueError("Place flags before positional operands")
        if rule.option in options:
            raise ValueError(f"Repeated option: {rule.option}")
        options[rule.option] = index
        options_ended = rule.ends_options
        if rule.takes is None:
            continue
        following = next(arguments, None)
        if following is None or following[1][1] != rule.takes:
            raise ValueError(f"{token[0]} requires a following {rule.takes} token")
        _matching_rule(following[1], spec)
    return ParsedCommand(tokens, operands, options)


def validate_tokens(
    tokens: list[TaggedToken], spec: TaggedCommandSpec
) -> ParsedCommand:
    """Validate the command token, argument syntax, and global option conflicts."""
    if not tokens or tokens[0] != spec.command:
        raise ValueError(f"First token must be {list(spec.command)!r}")
    parsed = _parse_arguments(tokens, spec)
    for left, right in spec.forbidden_pairs:
        if left in parsed.options and right in parsed.options:
            raise ValueError(f"Options {left} and {right} cannot be used together")
    return parsed


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
    """Validate transfer roots, retaining GNU's contents-copy operand spelling."""
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


def prepare_read(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require READ on regular files, preserving repeated operands and stdin."""
    first_operand = parsed.operands[0] if parsed.operands else len(parsed.tokens)
    argv = parsed.argv[:first_operand]
    operations: list[tuple[Operation, Path]] = []
    for index in parsed.operands:
        value, tag = parsed.tokens[index]
        if (value, tag) == ("-", "ARG"):
            argv.append(value)
            continue
        if tag != "PTH":
            raise ValueError("Reader operands must be PTH or stdin '-' tagged ARG")
        for spelling in expand_source_path(value, base):
            path = resolve_literal_path(spelling, base)
            file_stat = Path(spelling).stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"Reader path must be an existing regular file: {path}"
                )
            if file_stat.st_nlink > 1:
                raise ValueError(f"Hard-linked files are unsupported: {path}")
            argv.append(spelling)
            operations.append((Operation.READ, path))
    return PreparedCommand(
        ready=CommandReady(
            command_name=argv[0],
            argv=argv,
            base_workdir=base,
            stdin="",
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


def prepare_counted_read(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Check head/tail counts before preparing their regular-file operands."""
    for option in ("-n", "-c"):
        if option in parsed.options:
            value = parsed.tokens[parsed.options[option] + 1][0]
            if re.fullmatch(r"[0-9]+", value) is None:
                raise ValueError(
                    f"{option} requires an unsigned decimal count tagged ARG"
                )
    return prepare_read(parsed, base)
