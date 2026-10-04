"""Guarded cp token policy and permission preparation."""

import re
from pathlib import Path

from roboz.shed.models import Operation

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec, TokenRule
from .transfers import (
    _TRANSFER_TOKENS,
    _prepared_transfer,
    _transfer_arguments,
    expand_transfers,
)


def prepare_cp(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require source READ and effective destination CREATE for files and trees."""
    argv, transfers = _transfer_arguments(parsed, base)
    if "-R" not in parsed.options and any(source.is_dir() for source, _ in transfers):
        raise ValueError("Directory copies require -r, -R, or --recursive")
    return _prepared_transfer(argv, expand_transfers(transfers), base, Operation.READ)


CP = CommandSpec(
    command=("cp", "CMD"),
    allowed=(
        *_TRANSFER_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-r|-R|--recursive"), option="-R"),
    ),
    forbidden_pairs=(("-t", "-T"),),
    prepare_command=prepare_cp,
)
