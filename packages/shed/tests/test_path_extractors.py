"""Tests for guarded CLI path-argument extraction."""

import pytest

from roboshed.tools.cli_commands.utilities.path_extractors import (
    PathExtractor,
    cat_path_args,
    cp_path_args,
    diff_path_args,
    find_path_args,
    gio_trash_path_args,
    grep_path_args,
    head_path_args,
    ls_path_args,
    mkdir_path_args,
    mv_path_args,
    resolve_path_indices,
    rg_path_args,
    tail_path_args,
    tee_path_args,
    touch_path_args,
    wc_path_args,
)


@pytest.mark.parametrize(
    ("extractor", "flag"),
    [
        (cat_path_args, "-n"),
        (diff_path_args, "-a"),
        (ls_path_args, "-a"),
        (mkdir_path_args, "-p"),
        (tee_path_args, "-a"),
        (touch_path_args, "-a"),
        (wc_path_args, "-l"),
    ],
)
def test_path_commands_keep_operands_before_and_after_options(
    extractor: PathExtractor,
    flag: str,
) -> None:
    assert extractor([flag, "first.txt", "second.txt"]) == [1, 2]
    assert extractor(["first.txt", flag]) == [0]
    assert extractor(["first.txt", flag, "second.txt"]) == [0, 2]
    assert extractor(["--", "-private", "--"]) == [1, 2]


@pytest.mark.parametrize("extractor", [grep_path_args, rg_path_args])
def test_search_commands_skip_pattern_before_paths(extractor: PathExtractor) -> None:
    """grep and rg treat the first positional as the pattern, not a path."""
    assert extractor(["-n", "TODO|FIXME", "src", "tests"]) == [2, 3]
    assert extractor(["TODO|FIXME"]) == []


def test_find_path_args_uses_leading_search_roots() -> None:
    """find resolves only roots before the first predicate or option token."""
    assert find_path_args([".", "src", "-name", "*.py"]) == [0, 1]
    assert find_path_args(["-name", "*.py"]) == []


def test_gio_trash_path_args_extracts_paths_after_subcommand() -> None:
    """gio trash treats trailing non-flag argv tokens after `trash` as paths."""
    assert gio_trash_path_args(["trash", "a", "b"]) == [1, 2]
    assert gio_trash_path_args(["trash", "-f", "x"]) == [2]
    assert gio_trash_path_args(["trash"]) == []
    assert gio_trash_path_args(["list", "a"]) == []


@pytest.mark.parametrize("extractor", [cp_path_args, mv_path_args])
def test_source_destination_commands_extract_path_operands(
    extractor: PathExtractor,
) -> None:
    assert extractor(["-v", "src.txt", "dst.txt"]) == [1, 2]
    assert extractor(["-t", "dest", "a.txt", "b.txt"]) == [1, 2, 3]
    assert extractor(["--", "-leading", "dest"]) == [1, 2]


@pytest.mark.parametrize("extractor", [head_path_args, tail_path_args])
def test_head_tail_path_args_skip_known_option_values(
    extractor: PathExtractor,
) -> None:
    """head and tail skip line/byte count values before collecting paths."""
    assert extractor(["-n", "50", "notes.txt"]) == [2]
    assert extractor(["--bytes", "20", "a.txt", "b.txt"]) == [2, 3]


@pytest.mark.parametrize("extractor", [head_path_args, tail_path_args])
def test_head_tail_path_args_honor_double_dash(extractor: PathExtractor) -> None:
    """After `--`, head and tail treat all remaining tokens as paths."""
    assert extractor(["--", "-leading-dash.txt", "notes.txt"]) == [1, 2]


def test_resolve_path_indices_filters_and_sorts_extractor_output() -> None:
    """The resolver entry point keeps extractor outputs in argv index form."""

    def unordered_with_invalid_indices(argv: list[str]) -> list[int]:
        return [2, -1, 0, len(argv)]

    assert resolve_path_indices(["a", "b", "c"], unordered_with_invalid_indices) == [
        0,
        2,
    ]
    assert resolve_path_indices(["a"], None) == []


@pytest.mark.parametrize("extractor", [grep_path_args, rg_path_args])
@pytest.mark.parametrize(
    ("argv", "indices"),
    [
        (["needle", "--", "-private"], [2]),
        (["--", "-pattern", "-private"], [2]),
        (["-e", "needle", "private", "-n"], [2]),
        (["private", "-e", "needle"], [0]),
        (["-in", "pattern", "private"], [2]),
        (["-ne", "-pattern", "private"], [2]),
        (["--regexp=needle", "private"], [1]),
        (["--max-count", "1", "needle", "private"], [3]),
        (["-m1", "needle", "private"], [2]),
        (["needle", "-"], []),
    ],
)
def test_search_grammar_preserves_pattern_and_operand_roles(
    extractor: PathExtractor, argv: list[str], indices: list[int]
) -> None:
    assert extractor(argv) == indices


def test_rg_files_mode_has_no_pattern_and_glob_values_are_not_paths() -> None:
    assert rg_path_args(["--files", "private"]) == [1]
    assert rg_path_args(["-g", "*.py", "needle", "src"]) == [3]
    assert rg_path_args(["--glob=*.py", "needle", "src"]) == [2]


@pytest.mark.parametrize(
    ("extractor", "argv", "indices"),
    [
        (head_path_args, ["a", "-n", "2", "b"], [0, 3]),
        (tail_path_args, ["-n+2", "a"], [1]),
        (head_path_args, ["--lines=2", "-"], []),
        (mkdir_path_args, ["-m", "700", "a", "-p"], [2]),
        (touch_path_args, ["-d", "yesterday", "a"], [2]),
        (diff_path_args, ["-I", "^#", "a", "b"], [2, 3]),
        (ls_path_args, ["--color", "a"], [1]),
        (ls_path_args, ["--color=always", "a"], [1]),
        (find_path_args, ["-L", "a", "(", "-name", "*.py", ")"], [1]),
        (find_path_args, ["-name", "-exec"], []),
        (tee_path_args, ["-"], [0]),
        (cp_path_args, ["--", "-t", "a", "b"], [1, 2, 3]),
        (cp_path_args, ["-t", "--", "a"], [1, 2]),
    ],
)
def test_command_specific_option_values(
    extractor: PathExtractor, argv: list[str], indices: list[int]
) -> None:
    assert extractor(argv) == indices


@pytest.mark.parametrize(
    ("extractor", "argv"),
    [
        (cat_path_args, ["--unknown", "a"]),
        (cat_path_args, ["--number=yes", "a"]),
        (grep_path_args, ["-e"]),
        (rg_path_args, ["--glob"]),
        (head_path_args, ["a", "--lines"]),
        (cp_path_args, ["-t"]),
        (cp_path_args, ["-t", "a", "-t", "b", "c"]),
        (mv_path_args, ["-T", "-t", "a", "b"]),
        (cp_path_args, ["-t", "dest*", "src"]),
        (find_path_args, [".", "-name"]),
    ],
)
def test_incomplete_or_unsupported_grammar_is_rejected(
    extractor: PathExtractor, argv: list[str]
) -> None:
    with pytest.raises(ValueError):
        extractor(argv)
