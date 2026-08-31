from __future__ import annotations

from pathlib import Path

import pytest

from roboz.runtime.events import RunLifecycleEvent
from roboz.runtime.persistence import (
    RunStatus,
    active_agent_names,
    active_marker_paths,
    atomic_write_json,
    clear_active_markers,
    clear_conversation_active,
    mark_conversation_active,
)
from roboz.runtime.sinks import PersistenceSink


def _event(*, kind: str, agent: str, status: RunStatus | None = None):
    return RunLifecycleEvent(
        kind=kind,  # type: ignore[arg-type]
        agent_name=agent,
        sequence=1,
        status=status,
    )


def test_persistence_sink_tracks_active_and_terminal_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    sink = PersistenceSink.for_path(root / "orchestrator")
    sink(_event(kind="started", agent="orchestrator"))
    (marker,) = active_marker_paths(root, {"orchestrator"})
    assert active_agent_names(root, {"orchestrator"}) == {"orchestrator"}
    assert marker.read_bytes() == b""
    sink(_event(kind="stopped", agent="orchestrator", status=RunStatus.COMPLETED))
    assert active_marker_paths(root, {"orchestrator"}) == ()


def test_parallel_same_agent_invocations_have_independent_markers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    first = PersistenceSink.for_path(root / "worker")
    second = PersistenceSink.for_path(root / "worker")
    started = _event(kind="started", agent="worker")
    first(started)
    second(started)
    assert len(active_marker_paths(root, {"worker"})) == 2
    first(_event(kind="stopped", agent="worker", status=RunStatus.COMPLETED))
    assert len(active_marker_paths(root, {"worker"})) == 1
    second(_event(kind="stopped", agent="worker", status=RunStatus.CANCELLED))
    assert active_agent_names(root, {"worker"}) == set()


def test_boot_cleanup_removes_active_markers_without_reading_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "logs"
    sink = PersistenceSink.for_path(root / "orchestrator")
    sink(_event(kind="started", agent="orchestrator"))
    assert sink.conversations_location is not None
    conversation = sink.conversations_location

    def reject_read(*_args, **_kwargs):
        raise AssertionError("boot cleanup must not read conversations")

    monkeypatch.setattr(Path, "read_text", reject_read)
    assert clear_active_markers(root) == 1
    assert active_marker_paths(root, {"orchestrator"}) == ()
    assert conversation.exists()


def test_atomic_write_json_replaces_existing_document(tmp_path: Path) -> None:
    target = tmp_path / "conversation.json"
    atomic_write_json(target, {"version": 1})
    atomic_write_json(target, {"version": 2, "message": "ääkkönen"})
    assert target.read_text(encoding="utf-8") == '{"version": 2, "message": "ääkkönen"}'
    assert list(tmp_path.iterdir()) == [target]


def test_conversation_marker_accepts_hub_style_identifier(tmp_path: Path) -> None:
    agent_dir = tmp_path / "logs" / "orchestrator"

    marker = mark_conversation_active(
        agent_dir=agent_dir,
        conversation_id="stale-preboot-librarian-1234",
    )

    assert marker.parent == agent_dir / ".lifecycle" / "active"
    assert marker.name == "stale-preboot-librarian-1234.run"
    clear_conversation_active(
        agent_dir=agent_dir,
        conversation_id="stale-preboot-librarian-1234",
    )
    assert not marker.exists()


@pytest.mark.parametrize(
    "conversation_id",
    [
        "",
        ".",
        "..",
        "../outside",
        "nested/run",
        "/tmp/run",
        r"..\outside",
        r"C:\tmp\run",
    ],
)
def test_conversation_marker_rejects_unsafe_identifiers(
    tmp_path: Path, conversation_id: str
) -> None:
    agent_dir = tmp_path / "logs" / "orchestrator"

    with pytest.raises(ValueError, match="conversation_id"):
        mark_conversation_active(
            agent_dir=agent_dir,
            conversation_id=conversation_id,
        )
    with pytest.raises(ValueError, match="conversation_id"):
        clear_conversation_active(
            agent_dir=agent_dir,
            conversation_id=conversation_id,
        )

    assert not (agent_dir / ".lifecycle").exists()
