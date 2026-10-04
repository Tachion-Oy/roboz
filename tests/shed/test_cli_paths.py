"""Literal path resolution preserves the file CLI's filesystem restrictions."""

from pathlib import Path

import pytest

from roboz.shed.tools.cli_commands.paths import resolve_literal_path


def test_literal_paths_resolve_against_base(tmp_path: Path) -> None:
    base = tmp_path / "base"
    (base / "nested").mkdir(parents=True)

    assert resolve_literal_path("nested/../file", base) == base / "file"
    assert resolve_literal_path(str(tmp_path / "outside"), base) == tmp_path / "outside"


def test_literal_paths_reject_symlinks_before_parent_normalization(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    (tmp_path / "link").symlink_to(directory, target_is_directory=True)

    with pytest.raises(ValueError, match="Symlinks are unsupported"):
        resolve_literal_path("link/../file", tmp_path)


@pytest.mark.parametrize("entry_kind", ["file", "directory", "missing"])
def test_literal_trailing_slash_requires_an_existing_entry_to_be_a_directory(
    tmp_path: Path, entry_kind: str
) -> None:
    entry = tmp_path / "entry"
    if entry_kind == "file":
        entry.touch()
    elif entry_kind == "directory":
        entry.mkdir()

    if entry_kind == "file":
        with pytest.raises(ValueError, match="A trailing slash requires a directory"):
            resolve_literal_path("entry/", tmp_path)
    else:
        assert resolve_literal_path("entry/", tmp_path) == entry
