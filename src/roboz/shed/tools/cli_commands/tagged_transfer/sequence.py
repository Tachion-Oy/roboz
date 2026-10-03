"""Consume validated tagged streams according to Bash control operators."""

from .contracts import TaggedToken


def split_command(
    tokens: list[TaggedToken],
) -> tuple[list[TaggedToken], list[TaggedToken]]:
    """Return the current command and the remaining stream, starting at its CTL."""
    end = next((i for i, (_, tag) in enumerate(tokens) if tag == "CTL"), len(tokens))
    return tokens[:end], tokens[end:]


def next_command(
    tokens: list[TaggedToken], returncode: int
) -> tuple[list[TaggedToken], bool]:
    """Select the next step; pipes inherit whether their pipeline is enabled."""
    run_pipeline = True
    for index, (operator, tag) in enumerate(tokens):
        if tag != "CTL":
            continue
        match operator:
            case "&&":
                run_pipeline = returncode == 0
            case "||":
                run_pipeline = returncode != 0
            case ";":
                run_pipeline = True
        if run_pipeline:
            return tokens[index + 1 :], operator == "|"
    return [], False
