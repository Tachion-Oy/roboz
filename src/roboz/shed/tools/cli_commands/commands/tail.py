"""Guarded tail token policy and regular-file requirements."""

import re

from ..contracts import CommandSpec, TokenRule
from .readers import _READ_TOKENS, prepare_counted_read

TAIL = CommandSpec(
    command=("tail", "CMD"),
    allowed=(
        *_READ_TOKENS,
        TokenRule(tag="ARG", pattern=re.compile(r"[0-9]+")),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-n|--lines"), option="-n", takes="ARG"
        ),
        TokenRule(
            tag="FLG", pattern=re.compile(r"-c|--bytes"), option="-c", takes="ARG"
        ),
        TokenRule(tag="FLG", pattern=re.compile(r"-q|--quiet|--silent"), option="-q"),
        TokenRule(tag="FLG", pattern=re.compile(r"-v|--verbose"), option="-v"),
    ),
    forbidden_pairs=(("-n", "-c"), ("-q", "-v")),
    prepare_command=prepare_counted_read,
)
