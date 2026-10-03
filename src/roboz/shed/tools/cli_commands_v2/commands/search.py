"""Pattern, operand, and file validation shared by tagged grep and rg."""

import re
import shlex
import stat
from os import scandir
from pathlib import Path
from time import monotonic

from roboz.shed.models import CommandReady
from roboz.shed.tools.cli_commands.utilities.constants import SUBPROCESS_TIMEOUT_SECONDS

from ..contracts import ParsedCommand, TokenRule
from ..paths import expand_source_path
from ..tokens import END_OPTIONS, PATH_TOKEN
from .writers import inspect_file_path


SEARCH_TOKENS = (
    END_OPTIONS,
    PATH_TOKEN,
    TokenRule(tag="ARG", pattern=re.compile(r"[^\x00]*", re.DOTALL)),
    TokenRule(tag="FLG", pattern=re.compile(r"-n|--line-number"), option="-n"),
    TokenRule(tag="FLG", pattern=re.compile(r"-i|--ignore-case"), option="-i"),
    TokenRule(tag="FLG", pattern=re.compile(r"-v|--invert-match"), option="-v"),
    TokenRule(tag="FLG", pattern=re.compile(r"-F|--fixed-strings"), option="-F"),
    TokenRule(tag="FLG", pattern=re.compile(r"-w|--word-regexp"), option="-w"),
    TokenRule(tag="FLG", pattern=re.compile(r"-x|--line-regexp"), option="-x"),
    TokenRule(tag="FLG", pattern=re.compile(r"-c|--count"), option="-c"),
    TokenRule(tag="FLG", pattern=re.compile(r"-l|--files-with-matches"), option="-l"),
    TokenRule(tag="FLG", pattern=re.compile(r"-q|--quiet"), option="-q"),
    TokenRule(tag="FLG", pattern=re.compile(r"-o|--only-matching"), option="-o"),
    TokenRule(tag="FLG", pattern=re.compile(r"-H|--with-filename"), option="-H"),
    TokenRule(
        tag="FLG", pattern=re.compile(r"-m|--max-count"), option="-m", takes="ARG"
    ),
    TokenRule(
        tag="FLG", pattern=re.compile(r"-A|--after-context"), option="-A", takes="ARG"
    ),
    TokenRule(
        tag="FLG", pattern=re.compile(r"-B|--before-context"), option="-B", takes="ARG"
    ),
    TokenRule(tag="FLG", pattern=re.compile(r"-C|--context"), option="-C", takes="ARG"),
)


def search_arguments(parsed: ParsedCommand, base: Path) -> tuple[list[str], list[str]]:
    """Separate the pattern from paths and protect it from native option parsing."""
    if not parsed.operands or parsed.tokens[parsed.operands[0]][1] != "ARG":
        raise ValueError("Search requires a pattern ARG before input PTHs")
    for option in ("-m", "-A", "-B", "-C"):
        if option in parsed.options:
            value = parsed.tokens[parsed.options[option] + 1][0]
            if re.fullmatch(r"[0-9]+", value) is None:
                raise ValueError(f"{option} requires an unsigned decimal ARG")
    first = parsed.operands[0]
    flags = parsed.argv[1:first]
    if flags and flags[-1] == "--":
        flags.pop()
    # Always use -e, so empty patterns and patterns starting with '-' are data.
    argv = [parsed.argv[0], *flags, "-e", parsed.tokens[first][0], "--"]
    paths: list[str] = []
    for index in parsed.operands[1:]:
        value, tag = parsed.tokens[index]
        if tag == "PTH":
            paths.extend(expand_source_path(value, base))
        elif value == "-":
            paths.append("-")
        else:
            raise ValueError("Search inputs must be PTH or stdin '-' tagged ARG")
    return argv, paths


def search_ready(argv: list[str], base: Path) -> CommandReady:
    """Build a native search command with empty stdin when no pipe is present."""
    return CommandReady(
        command_name=argv[0],
        argv=argv,
        base_workdir=base,
        stdin="",
        display_command=shlex.join(argv),
    )


def search_path(path_arg: str, base: Path) -> tuple[Path, bool]:
    """Require an existing directory or an unaliased regular file."""
    resolved_path, entry = inspect_file_path(path_arg, base)
    if entry is None:
        raise ValueError(f"Search path must exist: {resolved_path}")
    return resolved_path, stat.S_ISDIR(entry.st_mode)


def search_deadline() -> float:
    """Give recursive preparation the same time allowance as native execution."""
    return monotonic() + SUBPROCESS_TIMEOUT_SECONDS


def check_search_deadline(deadline: float) -> None:
    """Fail preparation when its shared traversal and ignore-check budget expires."""
    if monotonic() >= deadline:
        raise ValueError(
            f"Search preparation timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds"
        )


def recursive_children(
    directory: Path, deadline: float, *, follow: bool = False
) -> list[Path]:
    """Select direct children in reverse order for stack-based traversal.

    Following mode includes links and special entries so callers can reject them
    during path validation. Otherwise native search skips those entries.
    """
    children: list[Path] = []
    with scandir(directory) as entries:
        for entry in entries:
            check_search_deadline(deadline)
            mode = entry.stat(follow_symlinks=False).st_mode
            if follow or stat.S_ISREG(mode) or stat.S_ISDIR(mode):
                children.append(directory / entry.name)
    check_search_deadline(deadline)
    children.sort(reverse=True)
    check_search_deadline(deadline)
    return children
