"""Parse the explicitly supported option grammar without guessing at operands."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, auto


class _ValueMode(Enum):
    NONE = auto()
    OPTIONAL = auto()
    REQUIRED = auto()
    PATH = auto()


@dataclass
class Arguments:
    """Operand indices and options recognized in an unmodified argv list."""

    positionals: list[int] = field(default_factory=list)
    options: set[str] = field(default_factory=set)
    path_values: list[int] = field(default_factory=list)


def parse_options(
    argv: list[str],
    *,
    flags: str = "",
    values: str = "",
    optional_values: str = "",
    paths: str = "",
    stdin: bool = False,
    numeric: bool = False,
) -> Arguments:
    """Parse known options, preserving argv indices and the ``--`` boundary.

    Long option names must be complete. Required non-path values can be separate
    or attached; optional values must be attached. Path-valued options require a
    separate token so the existing resolver can replace the entire path token.
    Unknown options and missing values raise ``ValueError`` before execution.
    """
    modes = (
        dict.fromkeys(f"{flags} --help --version".split(), _ValueMode.NONE)
        | dict.fromkeys(optional_values.split(), _ValueMode.OPTIONAL)
        | dict.fromkeys(values.split(), _ValueMode.REQUIRED)
        | dict.fromkeys(paths.split(), _ValueMode.PATH)
    )
    result = Arguments()
    remaining = iter(enumerate(argv))
    for index, token in remaining:
        if token == "--":
            result.positionals.extend(index for index, _ in remaining)
            break
        if token == "-" or not token.startswith("-"):
            result.positionals.append(index)
            continue
        if numeric and token[1:].isdigit():
            continue
        _parse_option_token(token, modes, remaining, result)

    if stdin:
        result.positionals = [i for i in result.positionals if argv[i] != "-"]
    return result


def _split_options(
    token: str, modes: dict[str, _ValueMode]
) -> Iterator[tuple[str, str | None]]:
    """Split a long option or short-option cluster into names and attached values."""
    if token.startswith("--"):
        name, separator, attached = token.partition("=")
        yield name, attached if separator else None
        return

    for offset, char in enumerate(token[1:], start=1):
        name = "-" + char
        if modes.get(name, _ValueMode.NONE) != _ValueMode.NONE:
            yield name, token[offset + 1 :] or None
            return
        yield name, None


def _parse_option_token(
    token: str,
    modes: dict[str, _ValueMode],
    remaining: Iterator[tuple[int, str]],
    result: Arguments,
) -> None:
    """Validate one option token and record its options and any path value."""
    for name, attached in _split_options(token, modes):
        mode = modes.get(name)
        if mode is None or (mode == _ValueMode.NONE and attached is not None):
            raise ValueError(f"Argument {token!r} not allowed by command spec")
        if mode == _ValueMode.PATH and attached is not None:
            raise ValueError(
                f"Argument {token!r} not allowed by command spec; "
                f"use {name} with a separate path token"
            )
        result.options.add(name)
        path_index = _consume_value(name, attached, mode, remaining)
        if path_index is not None:
            result.path_values.append(path_index)


def _consume_value(
    name: str,
    attached: str | None,
    mode: _ValueMode,
    remaining: Iterator[tuple[int, str]],
) -> int | None:
    """Consume a required separate value, returning its index only for paths."""
    if mode in {_ValueMode.NONE, _ValueMode.OPTIONAL} or attached is not None:
        return None
    value = next(remaining, None)
    if value is None:
        raise ValueError(f"Option {name!r} requires a value")
    if mode != _ValueMode.PATH:
        return None

    index, path = value
    if any(char in path for char in "*?["):
        raise ValueError("Path-valued options require one literal path, not a glob")
    return index
