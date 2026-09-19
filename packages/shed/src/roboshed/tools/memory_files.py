"""Filesystem helpers shared by snapshot and consolidation tools."""

import os
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Final
from uuid import uuid4

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
    """Atomically publish a complete timestamped artifact, then remove ``replace``."""
    folder.mkdir(parents=True, exist_ok=True)
    temporary_path = folder / f".librarian-{uuid4().hex}.tmp"
    published_path: Path | None = None
    try:
        with temporary_path.open("x", encoding=UTF8_ENCODING) as temporary:
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())

        if pipe is not None:
            pipe.raise_if_cancelled()

        base_time = utc_now()
        for offset in count():
            stamp = base_time + timedelta(microseconds=offset)
            path = folder / f"{stamp.strftime(TIMESTAMP_STEM_FORMAT)}{suffix}"
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                continue
            published_path = path
            break
    finally:
        temporary_path.unlink(missing_ok=True)

    if published_path is None:
        raise RuntimeError("timestamp candidate generation exhausted")
    if replace is not None:
        replace.unlink(missing_ok=True)
    return published_path


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
