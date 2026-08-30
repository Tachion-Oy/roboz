"""Timestamped artifacts shared by conversation summarization tools."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from roboz.runtime import EventPipe

from roboz.standard.tools.agent_runtime.conversation_logs import utc_now

TIMESTAMP_STEM_FORMAT: Final[str] = "%Y%m%dT%H%M%S%fZ"


def relative_or_absolute(path: Path, *, base: Path) -> str:
    """Return ``path`` relative to ``base`` when possible, otherwise absolute-ish."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def parse_timestamp_stem(path: Path) -> datetime | None:
    """Parse a UTC timestamp from a file stem, or None when it does not conform."""
    try:
        return datetime.strptime(path.stem, TIMESTAMP_STEM_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def latest_timestamped_file(
    folder: Path, *, suffix: str
) -> tuple[datetime, Path] | tuple[None, None]:
    """Find the newest timestamp-named file directly inside ``folder``."""
    if not folder.is_dir():
        return None, None
    for path in sorted(folder.glob(f"*{suffix}"), reverse=True):
        stamp = parse_timestamp_stem(path)
        if stamp is not None:
            return stamp, path
    return None, None


def write_timestamped_file(
    folder: Path,
    body: str,
    *,
    suffix: str,
    replace: Path | None,
    pipe: EventPipe | None = None,
) -> Path:
    """Write ``body`` as a new timestamp-named file, then drop ``replace``."""
    path = folder / f"{utc_now().strftime(TIMESTAMP_STEM_FORMAT)}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if pipe is not None:
        pipe.raise_if_cancelled()
    path.write_text(body)
    if replace is not None:
        replace.unlink(missing_ok=True)
    return path


__all__ = [
    "latest_timestamped_file",
    "parse_timestamp_stem",
    "relative_or_absolute",
    "write_timestamped_file",
]
