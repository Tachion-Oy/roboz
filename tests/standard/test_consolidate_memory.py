"""Tests for persistent memory consolidation."""

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import Empty, Message
from roboz.runtime.persistence import (
    ConversationRun,
    RunStatus,
    clear_conversation_active,
    mark_conversation_active,
    message_to_logged_row,
    utc_iso_z,
)
from roboz.runtime import EventPipe
from roboz.models import Role
from roboz.llm import MockLLMEndpoint

from roboz.standard.tools.conversation_summarization.artifacts import TIMESTAMP_STEM_FORMAT
from roboz.standard.tools.conversation_summarization.consolidate.prompts import (
    CONSOLIDATE_MEMORY_INSTRUCTIONS,
)
from roboz.standard.tools.conversation_summarization.consolidate.tool import consolidate_memory
from roboz.standard.tools.conversation_summarization.consolidate.types import (
    ConsolidateMemoryCtx,
)

consolidate_module = importlib.import_module(
    "roboz.standard.tools.conversation_summarization.consolidate.tool"
)


def _write_snapshot(
    snapshot_root: Path,
    *,
    at: datetime,
    conversation_id: str = "conversation-1",
    content: str = "# Conversation Snapshot: orchestrator\n\nSnapshot memory.\n",
) -> Path:
    path = snapshot_root / conversation_id / f"{at.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_memory(memory_root: Path, *, at: datetime, content: str) -> Path:
    path = memory_root / f"{at.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_conversation(
    conversation_root: Path, *, status: RunStatus, agent_name: str = "orchestrator"
) -> None:
    started_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    run = ConversationRun(
        conversation_id="run",
        agent_name=agent_name,
        started_at=utc_iso_z(started_at),
        ended_at=None if status == "running" else utc_iso_z(started_at),
        status=status,
        messages=[
            message_to_logged_row(
                Message(role=Role.USER, content="hi"),
                message_id="m-1",
                sequence=1,
                created_at=started_at,
            )
        ],
    )
    path = conversation_root / "orchestrator" / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(), encoding="utf-8")
    if run.status is RunStatus.RUNNING:
        mark_conversation_active(agent_dir=path.parent, conversation_id=run.conversation_id)
    else:
        clear_conversation_active(agent_dir=path.parent, conversation_id=run.conversation_id)


def _ctx(
    *,
    snapshot_root: Path,
    memory_root: Path,
    conversation_root: Path,
    endpoint: MockLLMEndpoint,
    agent_names: set[str] | None = None,
    min_pending_snapshots: int = 3,
    max_pending_age_seconds: float = 3_600.0,
    pipe: EventPipe | None = None,
) -> ConsolidateMemoryCtx:
    return ConsolidateMemoryCtx(
        endpoint=endpoint,
        snapshot_root=snapshot_root,
        memory_root=memory_root,
        conversation_root=conversation_root,
        agent_names=agent_names or {"orchestrator"},
        min_pending_snapshots=min_pending_snapshots,
        max_pending_age_seconds=max_pending_age_seconds,
        max_chars=10_000,
        pipe=pipe,
    )


def test_consolidate_prompt_prioritizes_user_authored_messages() -> None:
    assert "User-authored messages carried by the snapshots are paramount evidence" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "unless a later user message supersedes them" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "the user's message wins" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "merge in the snapshots" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "chronological state history" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "newer evidence supersedes older evidence" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "Facts that differ across time are state transitions" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )


def test_consolidate_prompt_turns_incidents_into_operational_safeguards() -> None:
    assert "re-query the source of truth" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "Never infer a user personality trait" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "opaque provider references" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "Do not preserve tool schemas" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "do not emit a `# Persistent Memory` title" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )


def test_memory_watermark_does_not_cover_snapshot_added_during_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status=RunStatus.COMPLETED)
    first_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    second_at = first_at + timedelta(minutes=1)
    _write_snapshot(snapshot_root, at=first_at, conversation_id="first")
    calls = 0

    def summarize(**kwargs) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            _write_snapshot(snapshot_root, at=second_at, conversation_id="second")
        return f"memory {calls}: {kwargs['conversation']}"

    monkeypatch.setattr(consolidate_module, "summarize_conversation_segment", summarize)
    tool = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )

    tool(input=Empty(), messages=[])
    tool(input=Empty(), messages=[])

    assert [path.stem for path in sorted(memory_root.glob("*.md"))] == [
        first_at.strftime(TIMESTAMP_STEM_FORMAT),
        second_at.strftime(TIMESTAMP_STEM_FORMAT),
    ]
    assert calls == 2


def test_consolidate_memory_writes_first_memory_when_threshold_is_met(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    now = datetime.now(timezone.utc)
    for index in range(3):
        _write_snapshot(
            snapshot_root,
            at=now - timedelta(minutes=index),
            conversation_id=f"conversation-{index}",
        )

    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Librarian pipeline."}])
    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
        )
    )(input=Empty(), messages=[])

    memories = list(memory_root.glob("*.md"))
    assert len(memories) == 1
    body = memories[0].read_text()
    assert body.startswith("# Persistent Memory")
    assert "Librarian pipeline" in body
    assert consolidate_module.PROVENANCE_MARKER in body
    assert "## Sources" in body
    assert "Previous memory:" not in body
    assert "pending=3, consolidated=1" in out.value


def test_consolidate_memory_rechecks_cancellation_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status=RunStatus.RUNNING)
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))
    runtime_pipe = EventPipe()
    runtime_pipe.initialize(dry_run=False, agent_name="librarian")

    def _cancelled_summary(*, pipe, **kwargs):
        del kwargs
        assert pipe is runtime_pipe
        pipe.cancel()
        return "late memory"

    monkeypatch.setattr(
        consolidate_module, "summarize_conversation_segment", _cancelled_summary
    )

    with pytest.raises(ExternalCallCancelledError):
        consolidate_memory(
            _ctx(
                snapshot_root=snapshot_root,
                memory_root=memory_root,
                conversation_root=conversation_root,
                endpoint=MockLLMEndpoint([]),
                min_pending_snapshots=1,
                pipe=runtime_pipe,
            )
        )(input=Empty(), messages=[])

    assert not list(memory_root.glob("*.md"))


def test_consolidate_memory_writes_provenance_for_pending_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="completed")
    _write_snapshot(
        snapshot_root,
        at=datetime.now(timezone.utc),
        conversation_id="conversation-1",
        content="# Conversation Snapshot: orchestrator\n\nSnapshot memory.\n",
    )
    captured_inputs: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured_inputs.append(conversation)
        return "## Active work\n- Consolidated."

    monkeypatch.setattr(
        consolidate_module, "summarize_conversation_segment", _fake_summary
    )

    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert len(captured_inputs) == 1
    assert "## New conversation snapshots" in captured_inputs[0]
    memories = list(memory_root.glob("*.md"))
    assert len(memories) == 1
    body = memories[0].read_text(encoding="utf-8")
    assert "Consolidated." in body
    assert "conversation-1" in body
    assert "orchestrator" in body
    assert "pending=1, consolidated=1" in out.value


def test_consolidate_memory_first_run_obeys_pending_threshold_while_active(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    _write_snapshot(snapshot_root, at=datetime.now(timezone.utc))

    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=0" in out.value
    assert not list(memory_root.glob("*.md"))


def test_consolidate_memory_skips_while_pending_buffer_is_below_threshold(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    now = datetime.now(timezone.utc)
    _write_memory(memory_root, at=now - timedelta(hours=1), content="Old memory.")
    _write_snapshot(snapshot_root, at=now - timedelta(minutes=1))

    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=0" in out.value
    assert len(list(memory_root.glob("*.md"))) == 1


@pytest.mark.parametrize(
    "status", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED]
)
def test_consolidate_memory_flushes_pending_when_project_is_idle(
    tmp_path: Path, status: RunStatus
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    # Watched conversation has finished: pending below threshold must still flush.
    _write_conversation(conversation_root, status=status)
    now = datetime.now(timezone.utc)
    _write_snapshot(snapshot_root, at=now - timedelta(minutes=1))

    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Flushed on idle."}])
    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=1" in out.value
    memories = list(memory_root.glob("*.md"))
    assert len(memories) == 1
    assert "Flushed on idle" in memories[0].read_text()


def test_consolidate_memory_flushes_stale_pending_snapshot_by_age(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    now = datetime.now(timezone.utc)
    _write_memory(memory_root, at=now - timedelta(hours=2), content="Old memory.")
    _write_snapshot(snapshot_root, at=now - timedelta(hours=1))

    endpoint = MockLLMEndpoint([{"value": "## Recent changes\n- Stale flush."}])
    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
            max_pending_age_seconds=60.0,
        )
    )(input=Empty(), messages=[])

    assert "pending=1, consolidated=1" in out.value


def test_consolidate_memory_keeps_previous_memory_file(tmp_path: Path) -> None:
    """The prior memory is retained for reference, not deleted.

    A new newest file is added each consolidation; only the newest is ever loaded
    by the orchestrator, and ``purge_memory`` bounds the folder's size.
    """
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    now = datetime.now(timezone.utc)
    previous = _write_memory(
        memory_root, at=now - timedelta(hours=1), content="Old memory."
    )
    for index in range(3):
        _write_snapshot(
            snapshot_root,
            at=now - timedelta(minutes=index),
            conversation_id=f"conversation-{index}",
        )

    endpoint = MockLLMEndpoint([{"value": "## Active work\n- Updated memory."}])
    out = consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
        )
    )(input=Empty(), messages=[])

    memories = sorted(memory_root.glob("*.md"))
    assert len(memories) == 2
    assert previous.exists()
    newest = memories[-1]
    assert newest != previous
    assert "Updated memory" in newest.read_text()
    assert "consolidated=1" in out.value
    assert "pending=3, consolidated=1" in out.value


def test_consolidate_memory_feeds_previous_memory_and_only_pending_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="running")
    now = datetime.now(timezone.utc)
    _write_memory(
        memory_root,
        at=now - timedelta(hours=1),
        content=(
            "# Persistent Memory\n\nPrevious memory baseline.\n\n"
            f"{consolidate_module.PROVENANCE_MARKER}\n"
            "## Sources\n"
            "- `snapshots/old/old.md` - orchestrator, conversation `old`\n"
        ),
    )
    _write_snapshot(
        snapshot_root,
        at=now - timedelta(hours=2),
        conversation_id="covered-conversation",
        content="Already consolidated.",
    )
    _write_snapshot(
        snapshot_root,
        at=now,
        conversation_id="fresh-conversation",
        content="Fresh snapshot content.",
    )
    captured_conversation = ""
    captured_max_chars = 0

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        nonlocal captured_conversation, captured_max_chars
        del endpoint, system_prompt, instructions
        captured_conversation = conversation
        captured_max_chars = max_chars
        return "## Recent changes\n- Folded fresh snapshot."

    monkeypatch.setattr(
        consolidate_module, "summarize_conversation_segment", _fake_summary
    )
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert "## Previous memory" in captured_conversation
    assert "Previous memory baseline" in captured_conversation
    assert consolidate_module.PROVENANCE_MARKER not in captured_conversation
    assert "snapshots/old/old.md" not in captured_conversation
    assert "Order: oldest to newest." in captured_conversation
    assert "### Snapshot 1 (recorded_at=" in captured_conversation
    assert "): fresh-conversation" in captured_conversation
    assert "Fresh snapshot content" in captured_conversation
    assert "Already consolidated" not in captured_conversation
    assert captured_max_chars == 10_000


def test_consolidate_memory_labels_pending_snapshots_oldest_to_newest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="completed")
    older_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    newer_at = older_at + timedelta(minutes=5)
    _write_snapshot(
        snapshot_root,
        at=newer_at,
        conversation_id="newer-conversation",
        content="Newer state: credibility file exists.",
    )
    _write_snapshot(
        snapshot_root,
        at=older_at,
        conversation_id="older-conversation",
        content="Older state: credibility folder is empty.",
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars, runtime
        captured.append(conversation)
        return "## Active work\n- Credibility file exists."

    monkeypatch.setattr(
        consolidate_module, "summarize_conversation_segment", _fake_summary
    )
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=MockLLMEndpoint([]),
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    conversation = captured[0]
    older_heading = (
        "### Snapshot 1 (recorded_at=2026-01-01T12:00:00+00:00): older-conversation"
    )
    newer_heading = (
        "### Snapshot 2 (recorded_at=2026-01-01T12:05:00+00:00): newer-conversation"
    )
    assert older_heading in conversation
    assert newer_heading in conversation
    assert conversation.index(older_heading) < conversation.index(newer_heading)
    assert "Order: oldest to newest." in conversation
    assert "supersede earlier mutable state" not in conversation


def test_consolidate_memory_prompt_keeps_markdown_inside_structured_value() -> None:
    assert "structured response's `value` field" in (
        consolidate_module.CONSOLIDATE_MEMORY_SYSTEM_PROMPT
    )
    assert "Output plain markdown only" not in (
        consolidate_module.CONSOLIDATE_MEMORY_SYSTEM_PROMPT
    )
    assert "Stay under 1200 words" not in CONSOLIDATE_MEMORY_INSTRUCTIONS


def test_strip_provenance_handles_marker_present_and_absent() -> None:
    with_marker = (
        "Header\n\nBody line\n\n"
        f"{consolidate_module.PROVENANCE_MARKER}\n"
        "## Sources\n"
        "- `snapshots/a/1.md` - orchestrator, conversation `a`\n"
    )
    stripped = consolidate_module.strip_provenance(with_marker)
    assert stripped == "Header\n\nBody line"
    assert consolidate_module.PROVENANCE_MARKER not in stripped

    without_marker = "Header\n\nBody line\n"
    assert consolidate_module.strip_provenance(without_marker) == "Header\n\nBody line"


def test_build_provenance_block_formats_order_and_optional_previous_memory(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    snapshot_root = project_root / "conversation_snapshots"
    memory_root = project_root / "persistent_memory"
    now = datetime.now(timezone.utc)
    old_snapshot = _write_snapshot(
        snapshot_root,
        at=now - timedelta(minutes=2),
        conversation_id="conversation-old",
        content="# Conversation Snapshot: orchestrator\n\nOlder.\n",
    )
    new_snapshot = _write_snapshot(
        snapshot_root,
        at=now - timedelta(minutes=1),
        conversation_id="conversation-new",
        content="# Conversation Snapshot: coder\n\nNewer.\n",
    )
    pending = [
        (now - timedelta(minutes=2), old_snapshot),
        (now - timedelta(minutes=1), new_snapshot),
    ]
    previous = _write_memory(
        memory_root,
        at=now - timedelta(hours=1),
        content="# Persistent Memory\n\nPrior.\n",
    )

    with_previous = consolidate_module.build_provenance_block(
        snapshot_root=snapshot_root,
        memory_root=memory_root,
        pending=pending,
        previous_memory_path=previous,
    )
    assert with_previous.startswith(consolidate_module.PROVENANCE_MARKER)
    assert "## Sources" in with_previous
    old_idx = with_previous.index("`conversation_snapshots/conversation-old/")
    new_idx = with_previous.index("`conversation_snapshots/conversation-new/")
    assert old_idx < new_idx
    assert "conversation `conversation-old`" in with_previous
    assert "conversation `conversation-new`" in with_previous
    assert "Previous memory: `persistent_memory/" in with_previous

    without_previous = consolidate_module.build_provenance_block(
        snapshot_root=snapshot_root,
        memory_root=memory_root,
        pending=pending,
        previous_memory_path=None,
    )
    assert "Previous memory:" not in without_previous


def test_consolidate_memory_provenance_does_not_accumulate_across_cycles(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    memory_root = tmp_path / "memory"
    conversation_root = tmp_path / "runs"
    _write_conversation(conversation_root, status="completed")
    now = datetime.now(timezone.utc)
    _write_snapshot(
        snapshot_root,
        at=now - timedelta(minutes=2),
        conversation_id="conversation-1",
        content="# Conversation Snapshot: orchestrator\n\nCycle one snapshot.\n",
    )

    endpoint = MockLLMEndpoint(
        [
            {"value": "## Active work\n- First cycle memory."},
            {"value": "## Active work\n- Second cycle memory."},
        ]
    )
    consolidate_memory(
        _ctx(
            snapshot_root=snapshot_root,
            memory_root=memory_root,
            conversation_root=conversation_root,
            endpoint=endpoint,
            min_pending_snapshots=1,
        )
    )(input=Empty(), messages=[])

    first_memory = max(memory_root.glob("*.md"))
    first_body = first_memory.read_text()
    assert first_body.count(consolidate_module.PROVENANCE_MARKER) == 1

    _write_snapshot(
        snapshot_root,
        at=now + timedelta(minutes=1),
        conversation_id="conversation-2",
        content="# Conversation Snapshot: coder\n\nCycle two snapshot.\n",
    )
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
    second_body = memories[-1].read_text()
    assert second_body.count(consolidate_module.PROVENANCE_MARKER) == 1
    assert (
        "Cycle one snapshot"
        not in second_body.split(consolidate_module.PROVENANCE_MARKER)[0]
    )
    assert "conversation `conversation-2`" in second_body
