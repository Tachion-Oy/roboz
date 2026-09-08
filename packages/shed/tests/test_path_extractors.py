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
    "extractor",
    [
        cat_path_args,
        diff_path_args,
        ls_path_args,
        mkdir_path_args,
        tee_path_args,
        touch_path_args,
        wc_path_args,
    ],
)
def test_trailing_path_commands_use_final_non_flag_tokens(
    extractor: PathExtractor,
) -> None:
    """Commands like cat, ls, tee, and mkdir use trailing operands as paths."""
    assert extractor(["-a", "first.txt", "second.txt"]) == [1, 2]
    assert extractor(["first.txt", "-a"]) == []


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
