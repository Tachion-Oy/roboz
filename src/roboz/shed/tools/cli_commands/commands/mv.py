"""Guarded mv token policy and permission preparation."""

from pathlib import Path

from roboz.shed.models import Operation

from ..contracts import ParsedCommand, PreparedCommand, CommandSpec
from .transfers import (
    _TRANSFER_TOKENS,
    _prepared_transfer,
    _transfer_arguments,
    expand_transfers,
)


def prepare_mv(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require DELETE/CREATE throughout a tree for same-filesystem moves."""
    argv, transfers = _transfer_arguments(parsed, base)
    for source, destination in transfers:
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise ValueError("Cross-filesystem moves are unsupported")
        if destination.is_dir() and any(destination.iterdir()):
            raise ValueError(f"Move destination directory must be empty: {destination}")
    return _prepared_transfer(argv, expand_transfers(transfers), base, Operation.DELETE)


MV = CommandSpec(
    command=("mv", "CMD"),
    allowed=_TRANSFER_TOKENS,
    forbidden_pairs=(("-t", "-T"),),
    prepare_command=prepare_mv,
)
