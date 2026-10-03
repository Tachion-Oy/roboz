"""Tagged tee options, inline stdin, and guarded output destinations."""

import os
import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import END_OPTIONS, PATH_TOKEN
from .writers import write_operations


def prepare_tee(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Prepare literal outputs and optional leading ARG content as stdin."""
    first_operand = parsed.operands[0] if parsed.operands else len(parsed.tokens)
    argv = parsed.argv[:first_operand]
    stdin = ""
    operations: list[tuple[Operation, Path]] = []
    for position, index in enumerate(parsed.operands):
        value, tag = parsed.tokens[index]
        if tag == "ARG":
            if position != 0:
                raise ValueError("tee accepts one content ARG before destination PTHs")
            stdin = value
            continue
        if "*" in value:
            raise ValueError("tee destination PTH must be literal; '*' is unsupported")
        path_arg = os.path.join(str(base), value)
        operations.extend(write_operations(path_arg, base))
        argv.append(path_arg)
    return PreparedCommand(
        ready=CommandReady(
            command_name="tee",
            argv=argv,
            base_workdir=base,
            stdin=stdin,
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


TEE = TaggedCommandSpec(
    command=("tee", "CMD"),
    allowed=(
        END_OPTIONS,
        PATH_TOKEN,
        TokenRule(tag="ARG", pattern=re.compile(r".*", re.DOTALL)),
        TokenRule(tag="FLG", pattern=re.compile(r"-a|--append"), option="-a"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_tee,
)
