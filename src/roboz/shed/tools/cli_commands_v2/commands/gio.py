"""Tagged gio trash syntax and DELETE requirements for entries and contents."""

import re
from pathlib import Path

from roboz.shed.models import Operation

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import PATH_TOKEN
from .deletions import prepare_deletion


def prepare_gio(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Accept only trash followed by paths, guarding the base when paths are omitted."""
    if "trash" not in parsed.options:
        raise ValueError("gio requires 'trash' tagged ARG before target PTHs")
    operands = [parsed.tokens[index][0] for index in parsed.operands]
    prepared = prepare_deletion(["gio", "trash"], operands, base, recursive=True)
    if not operands:
        return PreparedCommand(prepared.ready, [(Operation.DELETE, base)])
    return prepared


GIO = TaggedCommandSpec(
    command=("gio", "CMD"),
    allowed=(
        PATH_TOKEN,
        TokenRule(tag="ARG", pattern=re.compile(r"trash"), option="trash"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_gio,
)
