"""Tagged cat token policy and regular-file requirements."""

import re

from ..contracts import TaggedCommandSpec, TokenRule
from .readers import _READ_TOKENS, prepare_read

CAT = TaggedCommandSpec(
    command=("cat", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="FLG", pattern=re.compile(r"-n|--number"), option="-n"),
        TokenRule(tag="FLG", pattern=re.compile(r"-b|--number-nonblank"), option="-b"),
        TokenRule(tag="FLG", pattern=re.compile(r"-s|--squeeze-blank"), option="-s"),
        TokenRule(tag="FLG", pattern=re.compile(r"-E|--show-ends"), option="-E"),
        TokenRule(tag="FLG", pattern=re.compile(r"-T|--show-tabs"), option="-T"),
    ),
    forbidden_pairs=(),
    prepare_command=prepare_read,
)
