"""Consume command-token streams according to zsh control operators."""

from .contracts import Token


def split_command(
    tokens: list[Token],
) -> tuple[list[Token], list[Token]]:
    """Return the current command and the remaining stream, starting at its CTL."""
    end = next((i for i, (_, tag) in enumerate(tokens) if tag == "CTL"), len(tokens))
    return tokens[:end], tokens[end:]


def next_command(
    tokens: list[Token], returncode: int
) -> tuple[list[Token], bool]:
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
