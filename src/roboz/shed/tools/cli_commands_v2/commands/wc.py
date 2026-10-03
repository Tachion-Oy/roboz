"""Tagged wc token policy and regular-file requirements."""

import re

from ..contracts import TaggedCommandSpec, TokenRule
from .readers import _READ_TOKENS, prepare_read

WC = TaggedCommandSpec(
    command=("wc", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-l|--lines"), option="-l"),
        TokenRule(tag="FLG", pattern=re.compile(r"-w|--words"), option="-w"),
        TokenRule(tag="FLG", pattern=re.compile(r"-c|--bytes"), option="-c"),
        TokenRule(tag="FLG", pattern=re.compile(r"-m|--chars"), option="-m"),
        TokenRule(tag="FLG", pattern=re.compile(r"-L|--max-line-length"), option="-L"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_read,
)
