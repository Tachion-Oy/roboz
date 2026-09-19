"""Regression tests for atomic Librarian artifact publication."""

import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from roboshed.tools.memory_files import write_timestamped_file
from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime import EventPipe

memory_files = importlib.import_module("roboshed.tools.memory_files")


def test_timestamp_collision_publishes_two_complete_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant = datetime(2026, 9, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(memory_files, "utc_now", lambda: instant)

    first = write_timestamped_file(
        tmp_path, "first complete body", suffix=".md", replace=None
    )
    second = write_timestamped_file(
        tmp_path, "second complete body", suffix=".md", replace=None
    )

    assert first != second
    assert {path.read_text() for path in tmp_path.glob("*.md")} == {
        "first complete body",
        "second complete body",
    }
    assert not list(tmp_path.glob(".librarian-*.tmp"))


def test_cancellation_before_publication_removes_temporary_file(
    tmp_path: Path,
) -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="librarian")
    pipe.cancel()

    with pytest.raises(ExternalCallCancelledError):
        write_timestamped_file(
            tmp_path,
            "must not become visible",
            suffix=".md",
            replace=None,
            pipe=pipe,
        )

    assert not list(tmp_path.iterdir())
