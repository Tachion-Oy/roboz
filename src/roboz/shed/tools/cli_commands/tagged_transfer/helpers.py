"""Small validation and filesystem transfer helpers for tagged commands."""

from itertools import chain, combinations, product
from pathlib import Path

from .contracts import ParsedCommand, TaggedCommandSpec, TaggedToken, TokenRule


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
    return ParsedCommand([value for value, _ in tokens], operands, options)


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
    """Reject transfers whose roots could change another transfer's meaning."""
    sources = [source for source, _ in transfers]
    targets = [target for _, target in transfers]
    pairs = chain(
        combinations(sources, 2), combinations(targets, 2), product(sources, targets)
    )
    for left, right in pairs:
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise ValueError(
                f"Overlapping transfer paths are unsupported: {left}, {right}"
            )


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
    """Map every tree entry before guarding, including hidden and empty directories."""
    expanded: list[tuple[Path, Path]] = []
    source_ids: set[tuple[int, int]] = set()
    pending = list(reversed(transfers))
    while pending:
        source, target = pending.pop()
        _validate_transfer_source(source)
        source_stat = source.stat()
        source_ids.add((source_stat.st_dev, source_stat.st_ino))
        expanded.append((source, target))
        if source.is_dir():
            pending.extend(
                (child, target / child.name)
                for child in sorted(source.iterdir(), reverse=True)
            )
    # Parents precede children: a missing parent is a directory planned above.
    for source, target in expanded:
        _validate_transfer_target(target, source_ids, directory=source.is_dir())
    return expanded
