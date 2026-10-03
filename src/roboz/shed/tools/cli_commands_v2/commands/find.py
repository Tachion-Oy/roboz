"""Native find expressions with tagged roots and a read-only option allowlist."""

import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import PATH_TOKEN
from .discovery import discovery_paths


_VALUE_OPTIONS = (
    "-name", "-iname", "-path", "-ipath", "-type", "-size", "-mtime", "-mmin",
    "-maxdepth", "-mindepth",
)
_EXPRESSION_OPTIONS = (
    "-empty", "-depth", "-xdev", "-print", "-print0", "-prune", "-quit",
    "-a", "-and", "-o", "-or", "!", "-not", "(", ")",
)


def prepare_find(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Validate token roles, then let native find evaluate its own expressions.

    READ covers starting points only, as in v1. A directory authorization permits
    discovery below it; no descendant policy checks or expression interpreter
    are involved. -P keeps native traversal from following symbolic links.
    """
    tokens = iter(parsed.tokens[1:])
    token = next(tokens, None)
    while token in (("-P", "FLG"), ("--", "FLG")):
        token = next(tokens, None)
    roots: list[str] = []
    while token is not None and token[1] == "PTH":
        roots.append(token[0])
        token = next(tokens, None)
    argv = ["find", "-P"]
    operations: list[tuple[Operation, Path]] = []
    for root in roots or ["."]:
        for spelling, path in discovery_paths(root, base):
            if spelling.startswith("-") or spelling in {"!", "(", ")", ","}:
                spelling = "./" + spelling
            argv.append(spelling)
            operations.append((Operation.READ, path))
    while token is not None:
        value, tag = token
        if tag != "FLG" or value not in (*_VALUE_OPTIONS, *_EXPRESSION_OPTIONS):
            raise ValueError("find requires roots tagged PTH before its FLG expression")
        argv.append(value)
        if value in _VALUE_OPTIONS:
            argument = next(tokens, None)
            if argument is None or argument[1] != "ARG":
                raise ValueError(f"{value} requires a following ARG token")
            argv.append(argument[0])
        token = next(tokens, None)
    return PreparedCommand(
        ready=CommandReady(
            command_name="find",
            argv=argv,
            base_workdir=base,
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


FIND = TaggedCommandSpec(
    command=("find", "CMD"),
    # Roots precede predicates; repeated predicates and native expression syntax
    # belong to this command, rather than the generic flags-before-operands parser.
    allowed=(
        PATH_TOKEN,
        TokenRule(tag="ARG", pattern=re.compile(r"[^\x00]*", re.DOTALL)),
        TokenRule(
            tag="FLG",
            pattern=re.compile(
                "|".join(map(re.escape, (*_VALUE_OPTIONS, *_EXPRESSION_OPTIONS, "-P", "--")))
            ),
        ),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_find,
)
