"""Guarded ripgrep with a single preflight over files and recursive input trees."""

import re
import stat
from collections.abc import Callable
from itertools import product
from pathlib import Path

from roboz.shed.models import Operation

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec, TokenRule
from ..paths import preparation_deadline
from .search import (
    SEARCH_TOKENS,
    recursive_children,
    search_arguments,
    search_path,
    search_ready,
)
from .writers import inspect_file_path


_IGNORE_NAMES = (".gitignore", ".ignore", ".rgignore")
_RG_OPTIONS = ("--no-config", "--no-ignore-global", "--no-ignore-exclude")


def _input_paths(path_args: list[str], base: Path) -> tuple[list[Path], list[Path]]:
    """Validate explicit operands and separate files from directory roots."""
    files: list[Path] = []
    roots: list[Path] = []
    for path_arg in path_args:
        if path_arg == "-":
            continue
        resolved_path, directory = search_path(path_arg, base)
        if directory:
            roots.append(resolved_path)
        else:
            files.append(resolved_path)
    return files, roots


def _tree_paths(
    roots: list[Path], base: Path, check_deadline: Callable[[], None]
) -> tuple[set[Path], set[Path]]:
    """Validate candidate trees and retain their directories for ignore checks."""
    paths: set[Path] = set()
    directories: set[Path] = set()
    pending = list(reversed(roots))
    while pending:
        check_deadline()
        path, directory = search_path(str(pending.pop()), base)
        if path in paths:
            continue
        paths.add(path)
        if not directory:
            continue
        directories.add(path)
        pending.extend(recursive_children(path, check_deadline))
    return paths, directories


def _ignore_files(
    roots: list[Path], directories: set[Path], base: Path, check_deadline: Callable[[], None]
) -> list[Path]:
    """Validate potential ignore files in the tree and above its explicit roots."""
    ignore_directories = directories.copy()
    for root in roots:
        ignore_directories.update(root.parents)
    files: list[Path] = []
    for directory, name in product(sorted(ignore_directories), _IGNORE_NAMES):
        check_deadline()
        path, entry = inspect_file_path(str(directory / name), base)
        if entry is None:
            continue
        if stat.S_ISDIR(entry.st_mode):
            continue
        files.append(path)
    return files


def _recursive_reads(roots: list[Path], base: Path, *, ignore: bool) -> list[Path]:
    """Combine candidate-tree reads with any required ignore-file reads."""
    check_deadline = preparation_deadline("Search")
    paths, directories = _tree_paths(roots, base, check_deadline)
    if ignore:
        paths.update(_ignore_files(roots, directories, base, check_deadline))
    reads = sorted(paths)
    check_deadline()
    return reads


def prepare_rg(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Guard all candidate inputs before native matching and ignore filtering."""
    argv, paths = search_arguments(parsed, base)
    if not paths:
        paths = ["-"]
    files, roots = _input_paths(paths, base)
    if roots:
        ignore = "--no-ignore" not in parsed.options and "-uu" not in parsed.options
        files.extend(_recursive_reads(roots, base, ignore=ignore))
        options = _RG_OPTIONS
    else:
        # Explicit files override ignores. Avoid unrelated configuration reads.
        options = ("--no-config", "--no-ignore")
    ready = search_ready([argv[0], *options, *argv[1:], *paths], base)
    operations = [(Operation.READ, path) for path in dict.fromkeys(files)]
    return PreparedCommand(ready, operations)


RG = CommandSpec(
    command=("rg", "CMD"),
    allowed=(
        *SEARCH_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-I|--no-filename"), option="-I"),
        TokenRule(tag="FLG", pattern=re.compile(r"--hidden"), option="--hidden"),
        TokenRule(tag="FLG", pattern=re.compile(r"--no-ignore"), option="--no-ignore"),
        TokenRule(tag="FLG", pattern=re.compile(r"-uu"), option="-uu"),
    ),
    forbidden_pairs=(("-H", "-I"),),
    prepare_command=prepare_rg,
)
