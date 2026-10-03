"""Tagged GNU diff flags and exactly two guarded regular-file operands."""

import re
from pathlib import Path

from ..contracts import ParsedCommand, PreparedCommand, TaggedCommandSpec, TokenRule
from ..tokens import END_OPTIONS, PATH_TOKEN
from .readers import prepare_read


def prepare_diff(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Reuse reader checks and count expanded operands without deduplicating them."""
    prepared = prepare_read(parsed, base)
    first = parsed.operands[0] if parsed.operands else len(parsed.tokens)
    if len(prepared.ready.argv[first:]) != 2:
        raise ValueError("diff requires exactly two regular-file PTH operands after expansion")
    return prepared


DIFF = TaggedCommandSpec(
    command=("diff", "CMD"),
    allowed=(
        END_OPTIONS,
        PATH_TOKEN,
        TokenRule(tag="FLG", pattern=re.compile(r"-u|--unified"), option="-u"),
        TokenRule(tag="FLG", pattern=re.compile(r"-q|--brief"), option="-q"),
        TokenRule(tag="FLG", pattern=re.compile(r"-s|--report-identical-files"), option="-s"),
        TokenRule(tag="FLG", pattern=re.compile(r"-i|--ignore-case"), option="-i"),
        TokenRule(tag="FLG", pattern=re.compile(r"-w|--ignore-all-space"), option="-w"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_diff,
)
