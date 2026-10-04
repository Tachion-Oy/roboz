"""Physical working-directory output guarded by READ on the configured base."""

import re
import shlex
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec, TokenRule
from ..tokens import END_OPTIONS


def prepare_pwd(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Force physical output independently of PWD and POSIXLY_CORRECT."""
    argv = parsed.argv
    if "-P" not in parsed.options:
        argv.insert(1, "-P")
    return PreparedCommand(
        ready=CommandReady(
            command_name="pwd",
            argv=argv,
            base_workdir=base,
            display_command=shlex.join(argv),
        ),
        operations=[(Operation.READ, base)],
    )


PWD = CommandSpec(
    command=("pwd", "CMD"),
    allowed=(
        END_OPTIONS,
        TokenRule(tag="FLG", pattern=re.compile(r"-P|--physical"), option="-P"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_pwd,
)
