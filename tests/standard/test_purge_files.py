"""Tests for the shared `purge_files` tool and helper."""

import os
from pathlib import Path

from roboz.models import Empty

from roboz.standard.identifiers import PURGE_LOGS_TOOL_NAME
from roboz.standard.tools.agent_runtime import PurgeFilesCtx, purge_files
from roboz.standard.tools.agent_runtime.purge_files import purge_files_by_threshold


def _tool(folders: list[Path], max_files: int):
    return purge_files(
        PurgeFilesCtx(folders=folders, pattern="*.md", max_files=max_files)
    ).copy(name=PURGE_LOGS_TOOL_NAME)


def test_purge_files_tool_name_stable() -> None:
    assert _tool([], 3).name == PURGE_LOGS_TOOL_NAME


def test_purge_files_skips_when_threshold_not_reached(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    for ts in (1, 2, 3):
        (root / f"{ts}.md").write_text("# x", encoding="utf-8")
    out = _tool([root], 3)(input=Empty(), messages=[])
    assert "below threshold (3/3)" in out.value
    assert sorted(p.name for p in root.glob("*.md")) == ["1.md", "2.md", "3.md"]


def test_purge_files_deletes_oldest_when_over_threshold(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    first = root / "first.md"
    second = root / "second.md"
    third = root / "third.md"
    first.write_text("# 1", encoding="utf-8")
    second.write_text("# 2", encoding="utf-8")
    third.write_text("# 3", encoding="utf-8")
    t0 = 1_700_000_000
    os.utime(first, (t0, t0))
    os.utime(second, (t0 + 50, t0 + 50))
    os.utime(third, (t0 + 100, t0 + 100))
    out = _tool([root], 2)(input=Empty(), messages=[])
    assert "deleted 1, kept 2 of 3" in out.value
    assert sorted(p.name for p in root.glob("*.md")) == ["second.md", "third.md"]


def test_purge_files_tiebreaks_by_path_when_mtime_equal(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    alpha = root / "alpha.md"
    beta = root / "beta.md"
    alpha.write_text("# a", encoding="utf-8")
    beta.write_text("# b", encoding="utf-8")
    t0 = 1_700_000_000
    os.utime(alpha, (t0, t0))
    os.utime(beta, (t0, t0))
    _tool([root], 1)(input=Empty(), messages=[])
    assert sorted(p.name for p in root.glob("*.md")) == ["beta.md"]


def test_purge_files_recurses_and_ignores_non_matching(tmp_path: Path) -> None:
    root = tmp_path / "files"
    nested = root / "2024" / "05" / "31"
    nested.mkdir(parents=True)
    (nested / "a.md").write_text("# a", encoding="utf-8")
    (nested / "b.md").write_text("# b", encoding="utf-8")
    (nested / "other.json").write_text("{}", encoding="utf-8")
    _tool([root], 1)(input=Empty(), messages=[])
    assert sorted(p.name for p in root.rglob("*.md")) == ["b.md"]
    assert (nested / "other.json").is_file()


def test_purge_files_ignores_missing_folders() -> None:
    out = _tool([Path("/definitely/missing/folder/path")], 1)(
        input=Empty(), messages=[]
    )
    assert "below threshold (0/1)" in out.value


def test_purge_files_by_threshold_ignores_missing_folders() -> None:
    out = purge_files_by_threshold(
        folders=[Path("/definitely/missing/path")], pattern="*.md", max_files=1
    )
    assert out.value == "below threshold (0/1)"


def test_purge_files_deduplicates_overlapping_folders_before_threshold(
    tmp_path: Path,
) -> None:
    root = tmp_path / "files"
    nested = root / "nested"
    nested.mkdir(parents=True)
    old = root / "old.md"
    retained = nested / "retained.md"
    old.write_text("old", encoding="utf-8")
    retained.write_text("retained", encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(retained, (2, 2))

    out = purge_files_by_threshold(
        folders=[root, nested], pattern="*.md", max_files=1
    )

    assert "deleted 1, kept 1 of 2" in out.value
    assert not old.exists()
    assert retained.exists()
