"""Threshold-based artifact retention with optional empty-folder pruning."""

import errno
from pathlib import Path
from typing import Final

from roboz.models import All, Message, NO_MESSAGE, Str
from roboz.tooling.decorators import factory
from roboz.tools.memory_contexts import PurgeFilesCtx

_BENIGN_RMDIR_ERRNOS: Final[frozenset[int]] = frozenset(
    {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}
)
_MIN_FILE_LIMIT: Final[int] = 0


def collect_matching_files(*, folders: list[Path], pattern: str) -> list[Path]:
    """Collect regular files matching ``pattern`` beneath existing roots."""
    matches: list[Path] = []
    for folder in folders:
        if folder.is_dir():
            matches.extend(path for path in folder.rglob(pattern) if path.is_file())
    return matches


def _prune_empty_directories(folders: list[Path]) -> int:
    """Remove empty descendants without deleting any configured root folder."""
    directories = {
        path
        for folder in folders
        if folder.is_dir()
        for path in folder.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    pruned = 0
    for directory in sorted(
        directories,
        key=lambda path: (len(path.parts), str(path)),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError as error:
            if error.errno in _BENIGN_RMDIR_ERRNOS:
                continue
            raise
        pruned += 1
    return pruned


def purge_files_by_threshold(
    *,
    folders: list[Path],
    pattern: str,
    max_files: int,
    prune_empty_directories: bool = False,
) -> Str:
    """Delete oldest matches only when their total exceeds ``max_files``."""
    files = sorted(
        collect_matching_files(folders=folders, pattern=pattern),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
    )
    limit = max(_MIN_FILE_LIMIT, max_files)
    delete_count = max(_MIN_FILE_LIMIT, len(files) - limit)
    for file_path in files[:delete_count]:
        file_path.unlink(missing_ok=True)

    pruned = _prune_empty_directories(folders) if prune_empty_directories else None
    prune_result = f"; pruned_empty_dirs={pruned}" if pruned is not None else ""
    if len(files) <= limit:
        return Str(
            value=f"below threshold ({len(files)}/{limit}){prune_result}",
            truncation=NO_MESSAGE,
        )
    return Str(
        value=f"deleted {delete_count}, kept {limit} of {len(files)}{prune_result}",
        truncation=NO_MESSAGE,
    )


@factory
def purge_files(input: All, messages: list[Message], ctx: PurgeFilesCtx) -> Str:
    """Remove configured files that exceed the retention threshold."""
    del input, messages
    return purge_files_by_threshold(
        folders=ctx.folders,
        pattern=ctx.pattern,
        max_files=ctx.max_files,
        prune_empty_directories=ctx.prune_empty_directories,
    )


__all__ = ["collect_matching_files", "purge_files", "purge_files_by_threshold"]
