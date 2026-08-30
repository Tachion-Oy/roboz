"""Shared access to persisted conversation logs."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from roboz.runtime.persistence.schema import ConversationRun


def utc_now() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def load_conversation_run(path: Path) -> ConversationRun | None:
    """Return a parsed conversation run, or ``None`` for torn/unreadable files."""
    try:
        return ConversationRun.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


__all__ = ["load_conversation_run", "utc_now"]
