"""Validate tagged arguments against command token policies."""

import re

from .contracts import ParsedCommand, TaggedCommandSpec, TaggedToken, TokenRule

END_OPTIONS = TokenRule(
    tag="FLG", pattern=re.compile(r"--"), option="--", ends_options=True
)
PATH_TOKEN = TokenRule(tag="PTH", pattern=re.compile(r"[^?\[\]\x00]+", re.DOTALL))


def _matching_rule(token: TaggedToken, spec: TaggedCommandSpec) -> TokenRule:
    value, tag = token
    for rule in spec.allowed:
        if tag == rule.tag and rule.pattern.fullmatch(value):
            return rule
    raise ValueError(f"Unsupported {tag} token for {spec.name}: {value!r}")


def _parse_arguments(
    tokens: list[TaggedToken], spec: TaggedCommandSpec
) -> ParsedCommand:
    """Dispatch argument tokens to operands or options, consuming option values."""
    operands: list[int] = []
    options: dict[str, int] = {}
    options_ended = False
    arguments = iter(enumerate(tokens[1:], start=1))
    for index, token in arguments:
        rule = _matching_rule(token, spec)
        if rule.option is None:
            operands.append(index)
            continue
        if options_ended:
            raise ValueError("Options are not allowed after the option terminator")
        if operands:
            raise ValueError("Place flags before positional operands")
        if rule.option in options:
            raise ValueError(f"Repeated option: {rule.option}")
        options[rule.option] = index
        options_ended = rule.ends_options
        if rule.takes is None:
            continue
        following = next(arguments, None)
        if following is None or following[1][1] != rule.takes:
            raise ValueError(f"{token[0]} requires a following {rule.takes} token")
        _matching_rule(following[1], spec)
    return ParsedCommand(tokens, operands, options)


def validate_tokens(
    tokens: list[TaggedToken], spec: TaggedCommandSpec
) -> ParsedCommand:
    """Validate the command token, argument syntax, and global option conflicts."""
    if not tokens or tokens[0] != spec.command:
        raise ValueError(f"First token must be {list(spec.command)!r}")
    parsed = _parse_arguments(tokens, spec)
    for left, right in spec.forbidden_pairs:
        if left in parsed.options and right in parsed.options:
            raise ValueError(f"Options {left} and {right} cannot be used together")
    return parsed
