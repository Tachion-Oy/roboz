"""Filesystem helpers shared by snapshot and consolidation tools."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from roboz.runtime import EventPipe
from roboz.runtime.persistence import ConversationRun

TIMESTAMP_STEM_FORMAT: Final[str] = "%Y%m%dT%H%M%S%fZ"
MARKDOWN_SUFFIX: Final[str] = ".md"
JSON_SUFFIX: Final[str] = ".json"
UTF8_ENCODING: Final[str] = "utf-8"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def relative_or_absolute(path: Path, *, base: Path) -> str:
    """Render ``path`` relative to ``base`` when it lies beneath that base."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def parse_timestamp_stem(path: Path) -> datetime | None:
    """Parse a timestamp-named artifact, returning ``None`` for other names."""
    try:
        return datetime.strptime(path.stem, TIMESTAMP_STEM_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def latest_timestamped_file(
    folder: Path, *, suffix: str
) -> tuple[datetime, Path] | tuple[None, None]:
    """Find the newest conforming timestamp-named file directly in ``folder``."""
    if not folder.is_dir():
        return None, None
    for path in sorted(folder.glob(f"*{suffix}"), reverse=True):
        stamp = parse_timestamp_stem(path)
        if stamp is not None:
            return stamp, path
    return None, None


def load_conversation_run(path: Path) -> ConversationRun | None:
    """Load a complete run document, tolerating torn and invalid files."""
    try:
        return ConversationRun.model_validate_json(
            path.read_text(encoding=UTF8_ENCODING)
        )
    except (OSError, UnicodeDecodeError, ValidationError):
        return None


def write_timestamped_file(
    folder: Path,
    body: str,
    *,
    suffix: str,
    replace: Path | None,
    pipe: EventPipe | None = None,
) -> Path:
    """Write a new timestamp-named artifact, then optionally delete ``replace``."""
    path = folder / f"{utc_now().strftime(TIMESTAMP_STEM_FORMAT)}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if pipe is not None:
        pipe.raise_if_cancelled()
    path.write_text(body, encoding=UTF8_ENCODING)
    if replace is not None:
        replace.unlink(missing_ok=True)
    return path


__all__ = [
    "JSON_SUFFIX",
    "MARKDOWN_SUFFIX",
    "TIMESTAMP_STEM_FORMAT",
    "UTF8_ENCODING",
    "latest_timestamped_file",
    "load_conversation_run",
    "parse_timestamp_stem",
    "relative_or_absolute",
    "utc_now",
    "write_timestamped_file",
]
