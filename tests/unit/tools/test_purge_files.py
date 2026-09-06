"""Regression tests for threshold retention and directory pruning."""

import os
from pathlib import Path

from roboz import Ctx
from roboz.models import Empty
from roboz.tools import purge_files, purge_files_by_threshold
from roboz.tools._identifiers import PURGE_FILES_TOOL_NAME

_MARKDOWN_PATTERN = "*.md"
_BASE_MTIME_SECONDS = 1_700_000_000


def _tool(
    folders: list[Path],
    max_files: int,
    *,
    prune_empty_directories: bool = False,
):
    return purge_files(
        Ctx(
            folders=folders,
            pattern=_MARKDOWN_PATTERN,
            max_files=max_files,
            prune_empty_directories=prune_empty_directories,
        )
    ).copy(name=PURGE_FILES_TOOL_NAME)


def test_purge_tool_uses_stable_declared_name() -> None:
    assert _tool([], 3).name == PURGE_FILES_TOOL_NAME


def test_purge_skips_at_threshold(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.md").write_text("content", encoding="utf-8")

    result = _tool([root], 3)(input=Empty(), messages=[])

    assert result.value == "below threshold (3/3)"
    assert len(list(root.glob(_MARKDOWN_PATTERN))) == 3


def test_purge_deletes_oldest_and_tiebreaks_by_path(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    alpha = root / "alpha.md"
    beta = root / "beta.md"
    newest = root / "newest.md"
    for path in (alpha, beta, newest):
        path.write_text(path.name, encoding="utf-8")
    os.utime(alpha, (_BASE_MTIME_SECONDS, _BASE_MTIME_SECONDS))
    os.utime(beta, (_BASE_MTIME_SECONDS, _BASE_MTIME_SECONDS))
    os.utime(newest, (_BASE_MTIME_SECONDS + 1, _BASE_MTIME_SECONDS + 1))

    result = _tool([root], 2)(input=Empty(), messages=[])

    assert result.value == "deleted 1, kept 2 of 3"
    assert sorted(path.name for path in root.glob(_MARKDOWN_PATTERN)) == [
        "beta.md",
        "newest.md",
    ]


def test_purge_recurses_and_ignores_nonmatching_files(tmp_path: Path) -> None:
    root = tmp_path / "files"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "a.md").write_text("a", encoding="utf-8")
    (nested / "b.md").write_text("b", encoding="utf-8")
    other = nested / "other.json"
    other.write_text("{}", encoding="utf-8")

    _tool([root], 1)(input=Empty(), messages=[])

    assert len(list(root.rglob(_MARKDOWN_PATTERN))) == 1
    assert other.is_file()


def test_purge_prunes_empty_descendants_after_deletion(tmp_path: Path) -> None:
    root = tmp_path / "files"
    expired = root / "expired"
    retained = root / "retained"
    expired.mkdir(parents=True)
    retained.mkdir()
    old = expired / "old.md"
    new = retained / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    os.utime(old, (_BASE_MTIME_SECONDS, _BASE_MTIME_SECONDS))
    os.utime(new, (_BASE_MTIME_SECONDS + 1, _BASE_MTIME_SECONDS + 1))

    result = _tool([root], 1, prune_empty_directories=True)(input=Empty(), messages=[])

    assert result.value == "deleted 1, kept 1 of 2; pruned_empty_dirs=1"
    assert root.is_dir()
    assert not expired.exists()
    assert new.is_file()


def test_purge_prunes_existing_empty_tree_even_below_threshold(tmp_path: Path) -> None:
    root = tmp_path / "files"
    empty_leaf = root / "empty" / "nested"
    populated = root / "populated"
    empty_leaf.mkdir(parents=True)
    populated.mkdir()
    snapshot = populated / "snapshot.md"
    other = populated / "other.json"
    snapshot.write_text("snapshot", encoding="utf-8")
    other.write_text("{}", encoding="utf-8")

    result = _tool([root], 1, prune_empty_directories=True)(input=Empty(), messages=[])

    assert result.value == "below threshold (1/1); pruned_empty_dirs=2"
    assert root.is_dir()
    assert not (root / "empty").exists()
    assert snapshot.is_file()
    assert other.is_file()


def test_purge_preserves_empty_directories_by_default(tmp_path: Path) -> None:
    root = tmp_path / "files"
    empty = root / "empty"
    empty.mkdir(parents=True)

    result = _tool([root], 1)(input=Empty(), messages=[])

    assert result.value == "below threshold (0/1)"
    assert empty.is_dir()


def test_purge_ignores_missing_roots() -> None:
    missing = Path("/definitely/missing/roboz-retention-test")
    result = purge_files_by_threshold(
        folders=[missing], pattern=_MARKDOWN_PATTERN, max_files=1
    )

    assert result.value == "below threshold (0/1)"


def test_negative_threshold_is_normalized_to_zero(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    artifact = root / "artifact.md"
    artifact.write_text("content", encoding="utf-8")

    result = _tool([root], -10)(input=Empty(), messages=[])

    assert result.value == "deleted 1, kept 0 of 1"
    assert not artifact.exists()
