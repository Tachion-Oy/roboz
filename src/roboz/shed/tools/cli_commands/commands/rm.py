"""Guarded rm options and guarded deletion of named entries and recursive contents."""

import re
from pathlib import Path

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec, TokenRule
from ..tokens import END_OPTIONS, PATH_TOKEN
from .deletions import prepare_deletion


def prepare_rm(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Prepare deletion while leaving missing-target and no-operand errors to rm."""
    first = parsed.operands[0] if parsed.operands else len(parsed.tokens)
    return prepare_deletion(
        parsed.argv[:first],
        [parsed.tokens[index][0] for index in parsed.operands],
        base,
        recursive="-r" in parsed.options,
    )


RM = CommandSpec(
    command=("rm", "CMD"),
    allowed=(
        END_OPTIONS,
        PATH_TOKEN,
        TokenRule(tag="FLG", pattern=re.compile(r"-r|-R|--recursive"), option="-r"),
        TokenRule(tag="FLG", pattern=re.compile(r"-f|--force"), option="-f"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_rm,
)
