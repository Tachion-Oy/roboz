"""Tagged GNU grep options and guarded recursive or streamed inputs."""

import re
from pathlib import Path

from roboz.shed.models import Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from .search import (
    SEARCH_TOKENS,
    check_search_deadline,
    recursive_children,
    search_arguments,
    search_deadline,
    search_path,
    search_ready,
)


def _read_operations(
    paths: list[str], base: Path, *, recursive: bool, follow: bool
) -> list[tuple[Operation, Path]]:
    """Collect distinct READ requirements in operand and traversal order."""
    deadline = search_deadline()
    operations: list[tuple[Operation, Path]] = []
    pending = [spelling for spelling in reversed(paths) if spelling != "-"]
    seen: set[Path] = set()
    while pending:
        check_search_deadline(deadline)
        spelling = pending.pop()
        path, directory = search_path(spelling, base)
        if path in seen:
            continue
        seen.add(path)
        operations.append((Operation.READ, path))
        if not directory:
            continue
        if not recursive:
            raise ValueError("grep directory inputs require -r/--recursive or -R")
        pending.extend(
            str(child)
            for child in recursive_children(Path(spelling), deadline, follow=follow)
        )
    check_search_deadline(deadline)
    return operations


def prepare_grep(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Prepare native matching with READ requirements for files and input trees."""
    argv, paths = search_arguments(parsed, base)
    recursive = "-r" in parsed.options
    follow = recursive and parsed.argv[parsed.options["-r"]] in {
        "-R",
        "--dereference-recursive",
    }
    if not paths:
        paths = [str(base)] if recursive else ["-"]
    operations = _read_operations(paths, base, recursive=recursive, follow=follow)
    return PreparedCommand(search_ready([*argv, *paths], base), operations)


GREP = TaggedCommandSpec(
    command=("grep", "CMD"),
    allowed=(
        *SEARCH_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-E|--extended-regexp"), option="-E"),
        TokenRule(tag="FLG", pattern=re.compile(r"-h|--no-filename"), option="-h"),
        TokenRule(
            tag="FLG",
            pattern=re.compile(r"-r|--recursive|-R|--dereference-recursive"),
            option="-r",
        ),
    ),
    forbidden_pairs=(("-E", "-F"), ("-H", "-h")),
    prepare_command=prepare_grep,
)
