"""Argv path-index extraction for the file-command skill."""

from __future__ import annotations

from collections.abc import Callable

type PathExtractor = Callable[[list[str]], list[int]]


def resolve_path_indices(
    argv: list[str],
    extractor: PathExtractor | None,
) -> list[int]:
    """Return sorted, in-range path indices from a command's extractor."""
    if extractor is None:
        return []
    return sorted(i for i in extractor(argv) if 0 <= i < len(argv))


def grep_path_args(argv: list[str]) -> list[int]:
    """Return grep path operands.

    The guarded grep form expects argv to contain flags, then a search pattern,
    then optional paths. The first non-flag token is the pattern, and any later
    non-flag tokens are treated as paths to resolve and permission-check.
    """
    return _positionals_after_first_non_flag(argv)


def rg_path_args(argv: list[str]) -> list[int]:
    """Return ripgrep path operands.

    Like grep, the first non-flag token is the search pattern. Later non-flag
    tokens are paths. This intentionally keeps ripgrep extraction simple for the
    subset of argv shapes allowed by the guarded CLI specs.
    """
    return _positionals_after_first_non_flag(argv)


def cat_path_args(argv: list[str]) -> list[int]:
    """Return cat path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def head_path_args(argv: list[str]) -> list[int]:
    """Return head path operands.

    `head` accepts files among options. Values consumed by `-n`, `-c`,
    `--lines`, and `--bytes` are option values, not paths. The `--` marker ends
    option parsing, so later tokens are treated as paths even if they start with
    a dash.
    """
    return _positional_excluding_option_values(
        argv, options_with_values={"-n", "-c", "--lines", "--bytes"}
    )


def tail_path_args(argv: list[str]) -> list[int]:
    """Return tail path operands.

    `tail` follows the same path rules as `head`: skip values consumed by
    `-n`, `-c`, `--lines`, and `--bytes`, ignore other option tokens, and treat
    tokens after `--` as path operands.
    """
    return _positional_excluding_option_values(
        argv, options_with_values={"-n", "-c", "--lines", "--bytes"}
    )


def find_path_args(argv: list[str]) -> list[int]:
    """Return find search-root operands.

    In the supported find form, search roots come first and predicates/options
    start at the first flag-like token. Only those leading non-flag roots are
    resolved as paths; predicate arguments such as `*.py` remain ordinary argv.
    """
    return _leading_non_flag_indices(argv)


def ls_path_args(argv: list[str]) -> list[int]:
    """Return ls path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def wc_path_args(argv: list[str]) -> list[int]:
    """Return wc path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def diff_path_args(argv: list[str]) -> list[int]:
    """Return diff path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def tee_path_args(argv: list[str]) -> list[int]:
    """Return tee output path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def touch_path_args(argv: list[str]) -> list[int]:
    """Return touch path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def mkdir_path_args(argv: list[str]) -> list[int]:
    """Return mkdir path operands from the trailing non-flag argv tokens."""
    return _trailing_non_flag_indices(argv)


def cp_path_args(argv: list[str]) -> list[int]:
    """Return cp path operands; resolver assigns source/destination roles."""
    return _source_destination_path_args(argv)


def mv_path_args(argv: list[str]) -> list[int]:
    """Return mv path operands; resolver assigns source/destination roles."""
    return _source_destination_path_args(argv)


def _source_destination_path_args(argv: list[str]) -> list[int]:
    """Return operands for commands that transfer sources to a destination."""
    return _positional_excluding_option_values(argv, options_with_values=set())


def gio_trash_path_args(argv: list[str]) -> list[int]:
    """Return gio trash path operands from argv after the trash subcommand."""
    if not argv or argv[0] != "trash":
        return []
    return [i + 1 for i in _trailing_non_flag_indices(argv[1:])]


def _positionals_after_first_non_flag(argv: list[str]) -> list[int]:
    """Return every non-flag token after the first non-flag token."""
    positional = [i for i, tok in enumerate(argv) if not tok.startswith("-")]
    return positional[1:]


def _trailing_non_flag_indices(argv: list[str]) -> list[int]:
    """Return the final contiguous run of tokens that are not options."""
    for i in range(len(argv) - 1, -1, -1):
        if argv[i].startswith("-"):
            return list(range(i + 1, len(argv)))
    return list(range(len(argv)))


def _leading_non_flag_indices(argv: list[str]) -> list[int]:
    """Return the initial contiguous run of tokens that are not options."""
    indices: list[int] = []
    for i, tok in enumerate(argv):
        if tok.startswith("-"):
            break
        indices.append(i)
    return indices


def _positional_excluding_option_values(
    argv: list[str], options_with_values: set[str]
) -> list[int]:
    """Return positional tokens, skipping known option values."""
    indices: list[int] = []
    consume_next_as_value = False
    passthrough = False
    for i, tok in enumerate(argv):
        if passthrough:
            indices.append(i)
            continue
        if consume_next_as_value:
            consume_next_as_value = False
            continue
        if tok == "--":
            passthrough = True
            continue
        if tok in options_with_values:
            consume_next_as_value = True
            continue
        if tok.startswith("-"):
            continue
        indices.append(i)
    return indices
