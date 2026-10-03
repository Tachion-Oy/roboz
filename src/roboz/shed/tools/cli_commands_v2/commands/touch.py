"""Tagged touch timestamp options and guarded file creation or updates."""

import os
import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..paths import expand_source_path
from ..tokens import END_OPTIONS, PATH_TOKEN
from .writers import inspect_file_path, write_operations


def prepare_touch(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Prepare targets and an optional timestamp reference without changing files.

    Missing named targets are created unless -c is supplied. Patterns select
    existing entries; no matches fail before execution, even with -c, rather
    than creating a filename containing the unmatched wildcard.
    """
    if not parsed.operands:
        raise ValueError("touch requires at least one target PTH")
    if any(parsed.tokens[index][1] != "PTH" for index in parsed.operands):
        raise ValueError("touch operands must be PTH; ARG is only for -d or -t")
    argv = parsed.argv[: parsed.operands[0]]
    if "-t" in parsed.options:
        stamp = argv[parsed.options["-t"] + 1]
        if re.fullmatch(r"[0-9]{8}(?:[0-9]{2}){0,2}(?:\.[0-9]{2})?", stamp) is None:
            raise ValueError("-t requires [[CC]YY]MMDDhhmm[.ss] tagged ARG")
    operations: list[tuple[Operation, Path]] = []
    if "-r" in parsed.options:
        index = parsed.options["-r"] + 1
        if "*" in argv[index]:
            raise ValueError("touch reference PTH must be literal; '*' is unsupported")
        argv[index] = os.path.join(str(base), argv[index])
        reference, entry = inspect_file_path(argv[index], base)
        if entry is None:
            raise ValueError(f"Reference must exist: {reference}")
        operations.append((Operation.READ, reference))
    for index in parsed.operands:
        for path_arg in expand_source_path(parsed.tokens[index][0], base):
            operations.extend(
                write_operations(
                    path_arg,
                    base,
                    allow_directory=True,
                    no_create="-c" in parsed.options,
                )
            )
            argv.append(path_arg)
    return PreparedCommand(
        ready=CommandReady(
            command_name="touch",
            argv=argv,
            base_workdir=base,
            stdin="",
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


TOUCH = TaggedCommandSpec(
    command=("touch", "CMD"),
    allowed=(
        END_OPTIONS,
        PATH_TOKEN,
        TokenRule(tag="ARG", pattern=re.compile(r"[^\x00]*")),
        TokenRule(tag="FLG", pattern=re.compile(r"-a"), option="-a"),
        TokenRule(tag="FLG", pattern=re.compile(r"-m"), option="-m"),
        TokenRule(tag="FLG", pattern=re.compile(r"-c|--no-create"), option="-c"),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-d|--date"), option="-d", takes="ARG"
        ),
        TokenRule(tag="FLG", pattern=re.compile(r"-t"), option="-t", takes="ARG"),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-r|--reference"), option="-r", takes="PTH"
        ),
    ),
    forbidden_pairs=(("-t", "-d"), ("-t", "-r")),
    prepare_command=prepare_touch,
)
