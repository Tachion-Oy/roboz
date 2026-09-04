"""Regression tests for storage-backed persistent-memory consolidation."""

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.llm import MockLLMEndpoint, bind_endpoint
from roboz.models import Empty
from roboz.runtime import EventPipe, LOG_DATA_ATTRIBUTE
from roboz.runtime.persistence import mark_conversation_active
from roboz.tools import ConsolidateMemoryCtx, consolidate_memory
from roboz.tools.librarian_errors import LibrarianProviderRequestFailure
from roboz.tools.memory_files import TIMESTAMP_STEM_FORMAT

consolidate_module = importlib.import_module("roboz.tools.consolidate_memory")

_DEFAULT_AGENT = "orchestrator"
_MAX_CHARS = 10_000
_ONE_HOUR_SECONDS = 3_600.0


def _write_snapshot(
    snapshot_root: Path,
    *,
    at: datetime,
    conversation_id: str = "conversation-1",
    content: str = "# Conversation Snapshot: orchestrator\n\nSnapshot memory.\n",
) -> Path:
    path = snapshot_root / conversation_id / f"{at.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_memory(memory_root: Path, *, at: datetime, content: str) -> Path:
    path = memory_root / f"{at.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _mark_active(conversation_root: Path, *, agent_name: str = _DEFAULT_AGENT) -> None:
    mark_conversation_active(
        agent_dir=conversation_root / agent_name,
        conversation_id="active-run",
    )


def _ctx(
    *,
    snapshot_root: Path,
    memory_root: Path,
    conversation_root: Path,
    endpoint: MockLLMEndpoint,
    min_pending_snapshots: int = 3,
    max_pending_age_seconds: float = _ONE_HOUR_SECONDS,
    pipe: EventPipe | None = None,
) -> ConsolidateMemoryCtx:
    return ConsolidateMemoryCtx(
        endpoint=bind_endpoint(endpoint),
        snapshot_root=snapshot_root,
        memory_root=memory_root,
        conversation_root=conversation_root,
        agent_names={_DEFAULT_AGENT},
        min_pending_snapshots=min_pending_snapshots,
        max_pending_age_seconds=max_pending_age_seconds,
        max_chars=_MAX_CHARS,
        pipe=pipe,
    )


@pytest.mark.parametrize(
    "summary",
    [
        "## Active work\n- Librarian pipeline.",
        "# Persistent Memory\n\n## Active work\n- Librarian pipeline.",
    ],
)
def test_consolidation_writes_one_title_provenance_and_structured_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    summary: str,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _mark_active(conversation_root)
    now = datetime.now(timezone.utc)
    for index in range(3):
        _write_snapshot(
            snapshot_root,
            at=now - timedelta(minutes=index),
            conversation_id=f"conversation-{index}",
        )
    endpoint = MockLLMEndpoint([{"value": summary}])

    with caplog.at_level("INFO"):
        result = consolidate_memory(
            _ctx(
                snapshot_root=snapshot_root,
                memory_root=memory_root,
                conversation_root=conversation_root,
                endpoint=endpoint,
            )
        )(input=Empty(), messages=[])

    memories = list(memory_root.glob("*.md"))
    assert len(memories) == 1
    body = memories[0].read_text(encoding="utf-8")
    assert body.count(consolidate_module.PERSISTENT_MEMORY_TITLE) == 1
    assert body.count(consolidate_module.PROVENANCE_MARKER) == 1
    assert "## Sources" in body
    assert "pending=3, consolidated=1" in result.value
    record = next(
        item
        for item in caplog.records
        if item.getMessage().startswith("Librarian memory consolidated")
    )
    assert getattr(record, LOG_DATA_ATTRIBUTE)["pending_snapshots"] == 3


def test_active_project_obeys_pending_threshold(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _mark_active(conversation_root)
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))

    result = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert result.value == "consolidate_memory: pending=1, consolidated=0"
    assert not list(memory_root.glob("*.md"))


def test_idle_project_flushes_pending_below_threshold(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))
    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Idle flush."}])

    result = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=1" in result.value
    assert "Idle flush" in next(memory_root.glob("*.md")).read_text(encoding="utf-8")


def test_stale_pending_snapshot_flushes_while_active(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _mark_active(conversation_root)
    now = datetime.now(timezone.utc)
    _write_memory(memory_root, at=now - timedelta(hours=2), content="Old memory.")
    _write_snapshot(snapshot_root, at=now - timedelta(hours=1))
    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Stale flush."}])

    result = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
            max_pending_age_seconds=60.0,
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=1" in result.value


def test_summary_receives_previous_memory_without_provenance_and_only_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _mark_active(conversation_root)
    now = datetime.now(timezone.utc)
    _write_memory(
        memory_root,
        at=now - timedelta(hours=1),
        content=(
            "# Persistent Memory\n\nPrevious baseline.\n\n"
            f"{consolidate_module.PROVENANCE_MARKER}\n"
            "## Sources\n- old source\n"
        ),
    )
    _write_snapshot(
        snapshot_root,
        at=now - timedelta(hours=2),
        conversation_id="covered",
        content="Already consolidated.",
    )
    _write_snapshot(
        snapshot_root,
        at=now,
        conversation_id="fresh",
        content="Fresh snapshot.",
    )
    captured: list[tuple[str, int, float]] = []

    def fake_summary(**kwargs):
        captured.append(
            (
                kwargs["conversation"],
                kwargs["max_chars"],
                kwargs["max_chars_tolerance_percent"],
            )
        )
        return "## Active work\n- Folded."

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", fake_summary)
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    conversation, max_chars, tolerance = captured[0]
    assert "Previous baseline" in conversation
    assert consolidate_module.PROVENANCE_MARKER not in conversation
    assert "Fresh snapshot" in conversation
    assert "Already consolidated" not in conversation
    assert max_chars == _MAX_CHARS
    assert tolerance == 15.0


def test_pending_snapshots_are_supplied_oldest_to_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    older = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=5)
    _write_snapshot(
        snapshot_root,
        at=newer,
        conversation_id="newer",
        content="Newer state.",
    )
    _write_snapshot(
        snapshot_root,
        at=older,
        conversation_id="older",
        content="Older state.",
    )
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## Active work\n- Current state."

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", fake_summary)
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert captured[0].index("### Snapshot: older") < captured[0].index(
        "### Snapshot: newer"
    )


def test_previous_memory_is_retained_and_listed_as_provenance(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "conversation_snapshots"
    memory_root = tmp_path / "persistent_memory"
    conversation_root = tmp_path / "runs"
    now = datetime.now(timezone.utc)
    previous = _write_memory(
        memory_root,
        at=now - timedelta(hours=1),
        content="# Persistent Memory\n\nPrior.\n",
    )
    _write_snapshot(
        snapshot_root,
        at=now,
        conversation_id="conversation-new",
        content="# Conversation Snapshot: coder\n\nNew.\n",
    )
    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Updated."}])

    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    memories = sorted(memory_root.glob("*.md"))
    assert len(memories) == 2
    assert previous.exists()
    body = memories[-1].read_text(encoding="utf-8")
    assert "coder, conversation `conversation-new`" in body
    assert "Previous memory: `persistent_memory/" in body


def test_provenance_is_not_accumulated_across_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    now = datetime.now(timezone.utc)
    _write_memory(
        memory_root,
        at=now - timedelta(hours=1),
        content=(
            "# Persistent Memory\n\nPrior.\n\n"
            f"{consolidate_module.PROVENANCE_MARKER}\n## Sources\n- old\n"
        ),
    )
    _write_snapshot(snapshot_root, at=now, conversation_id="fresh")
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## Active work\n- Updated."

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", fake_summary)
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert consolidate_module.PROVENANCE_MARKER not in captured[0]
    newest = max(memory_root.glob("*.md"))
    assert newest.read_text(encoding="utf-8").count(
        consolidate_module.PROVENANCE_MARKER
    ) == 1


def test_consolidation_rechecks_cancellation_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="librarian")

    def cancel_summary(**kwargs):
        assert kwargs["pipe"] is pipe
        pipe.cancel()
        return "late memory"

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", cancel_summary)
    with pytest.raises(ExternalCallCancelledError):
        consolidate_memory(
            _ctx(
                snapshot_root=snapshot_root,
                memory_root=memory_root,
                conversation_root=conversation_root,
                endpoint=MockLLMEndpoint([]),
                min_pending_snapshots=1,
                pipe=pipe,
            )
        )(input=Empty(), messages=[])

    assert not list(memory_root.glob("*.md"))


def test_consolidation_skips_redundant_twin_when_watermark_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))

    def racing_summary(**kwargs):
        del kwargs
        _write_memory(
            memory_root,
            at=datetime.now(timezone.utc),
            content="competing memory",
        )
        return "redundant memory"

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", racing_summary)
    result = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert "superseded, consolidated=0" in result.value
    assert len(list(memory_root.glob("*.md"))) == 1


def test_consolidation_translates_provider_request_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))

    def failing_summary(**kwargs):
        del kwargs
        raise LLMProviderRequestError("bad request")

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", failing_summary)
    with pytest.raises(LibrarianProviderRequestFailure):
        consolidate_memory(
            _ctx(
                snapshot_root=snapshot_root,
                memory_root=memory_root,
                conversation_root=conversation_root,
                endpoint=MockLLMEndpoint([]),
                min_pending_snapshots=1,
            )
        )(input=Empty(), messages=[])


def test_strip_helpers_handle_owned_sections() -> None:
    text = (
        "# Persistent Memory\n\nBody\n\n"
        f"{consolidate_module.PROVENANCE_MARKER}\n## Sources\n- source\n"
    )
    without_provenance = consolidate_module.strip_provenance(text)
    assert without_provenance == "# Persistent Memory\n\nBody"
    assert consolidate_module.strip_leading_memory_title(without_provenance) == "Body"
