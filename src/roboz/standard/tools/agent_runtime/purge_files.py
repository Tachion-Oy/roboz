"""Shared threshold-based file purging tool and helpers."""

from dataclasses import dataclass
from pathlib import Path

from roboz.models import All, Message, Str
from roboz.models import NO_MESSAGE
from roboz import FactoryCtx, factory


@dataclass(frozen=True)
class PurgeFilesCtx(FactoryCtx):
    """Context for threshold-based file purging."""

    pattern: str
    max_files: int
    folders: list[Path]


def collect_matching_files(*, folders: list[Path], pattern: str) -> list[Path]:
    matches: list[Path] = []
    seen: set[Path] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                matches.append(path)
    return matches


def purge_files_by_threshold(
    *, folders: list[Path], pattern: str, max_files: int
) -> Str:
    files = sorted(
        collect_matching_files(folders=folders, pattern=pattern),
        key=lambda p: (p.stat().st_mtime_ns, str(p)),
    )
    limit = max(0, max_files)
    if len(files) <= limit:
        return Str(
            value=f"below threshold ({len(files)}/{limit})", truncation=NO_MESSAGE
        )
    delete_count = len(files) - limit
    for file_path in files[:delete_count]:
        file_path.unlink(missing_ok=True)
    return Str(
        value=f"deleted {delete_count}, kept {limit} of {len(files)}",
        truncation=NO_MESSAGE,
    )


@factory
def purge_files(input: All, messages: list[Message], ctx: PurgeFilesCtx) -> Str:
    """Delete oldest matching files only when total exceeds ``max_files``."""
    # `input` and `messages` are part of the required tool signature.
    return purge_files_by_threshold(
        folders=ctx.folders, pattern=ctx.pattern, max_files=ctx.max_files
    )


__all__ = [
    "PurgeFilesCtx",
    "collect_matching_files",
    "purge_files",
    "purge_files_by_threshold",
]
