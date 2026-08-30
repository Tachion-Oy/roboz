"""Tests for conversation memory snapshots."""

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import Empty, Message
from roboz.runtime.persistence.schema import (
    ConversationRun,
    RunMetadata,
    RunStatus,
    message_to_logged_row,
    utc_iso_z,
)
from roboz.runtime import EventPipe
from roboz.models import DEFAULT, NO_MESSAGE, TruncationSpec
from roboz.models import BaseNames, MessageKind, Role
from roboz.llm import MockLLMEndpoint

from roboz.standard.tools.conversation_summarization.snapshot.prompts import (
    SNAPSHOT_CONVERSATION_INSTRUCTIONS,
)
from roboz.standard.tools.conversation_summarization.artifacts import TIMESTAMP_STEM_FORMAT
from roboz.standard.tools.conversation_summarization.snapshot.tool import snapshot_conversations
from roboz.standard.tools.conversation_summarization.snapshot.types import (
    SnapshotConversationsCtx,
)

snapshot_module = importlib.import_module(
    "roboz.standard.tools.conversation_summarization.snapshot.tool"
)


def _row(
    *,
    sequence: int,
    created_at: datetime,
    content: str,
    role: Role = Role.USER,
    truncation: TruncationSpec = DEFAULT,
    message_kind: str | None = None,
):
    return message_to_logged_row(
        Message(
            role=role,
            content=content,
            truncation=truncation,
            message_kind=message_kind,
        ),
        message_id=f"m-{sequence}",
        sequence=sequence,
        created_at=created_at,
    )


def _write_run(
    root: Path,
    *,
    agent_name: str = "librarian",
    conversation_id: str = "conversation-1",
    parent_conversation_id: str | None = None,
    status: RunStatus = "running",
    agent_description: str | None = None,
    messages,
) -> None:
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_name=agent_name,
        parent_conversation_id=parent_conversation_id,
        started_at=utc_iso_z(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        status=status,
        metadata=RunMetadata(agent_description=agent_description),
        messages=list(messages),
    )
    path = root / "2026" / "01" / "01" / f"{conversation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json())


def _ctx(
    *,
    conversation_root: Path,
    snapshot_root: Path,
    endpoint: MockLLMEndpoint,
    memory_root: Path | None = None,
    agent_names: set[str] | None = None,
    threshold: int = 1,
    pipe: EventPipe | None = None,
) -> SnapshotConversationsCtx:
    return SnapshotConversationsCtx(
        endpoint=endpoint,
        conversation_root=conversation_root,
        snapshot_root=snapshot_root,
        memory_root=memory_root or snapshot_root.parent / "memory",
        agent_names=agent_names or {"librarian"},
        token_growth_threshold=threshold,
        max_chars=10_000,
        pipe=pipe,
    )


def test_snapshot_prompt_uses_previous_snapshot_only_for_grounding() -> None:
    assert "previous snapshot is context only" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert (
        "must never be used to suppress, deduplicate, or omit information"
        in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert (
        "Every snapshot must faithfully represent the conversation segment"
        in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Record only what is new in this segment" not in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Read the input as a timeline" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert "When facts clash, later evidence wins" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Never copy an earlier state claim or loose end forward" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "`terminal-completed`" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert "`terminal-failed`" in SNAPSHOT_CONVERSATION_INSTRUCTIONS


def test_snapshot_prompt_prioritizes_user_authored_messages() -> None:
    assert "User-authored messages are paramount evidence" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "User answers to agent questions are especially important" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "writes them to a file" in SNAPSHOT_CONVERSATION_INSTRUCTIONS


def test_snapshot_prompt_records_incident_evidence_without_emotional_noise() -> None:
    assert "preserve the evidence and action sequence" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Never promote an unsupported agent assertion into a fact" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "paraphrase insults, profanity, and emotional intensity" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )


def test_snapshot_conversations_writes_markdown_summary_when_threshold_is_met(
    tmp_path: Path,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="User wants a Librarian snapshot.",
            )
        ],
    )

    endpoint = MockLLMEndpoint([{"value": "## User goals\n- Remember the work."}])
    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=endpoint,
        )
    )(input=Empty(), messages=[])

    snapshots = list(snapshot_root.rglob("*.md"))
    assert len(snapshots) == 1
    assert (
        snapshots[0]
        .read_text()
        .startswith("# Conversation Snapshot: librarian")
    )
    assert "Remember the work" in snapshots[0].read_text()
    assert "created=1" in out.value


def test_snapshot_conversations_writes_directly_under_conversation_id(
    tmp_path: Path,
) -> None:
    """Snapshots are keyed by conversation id only — no per-agent folder layer.

    Two different agents in the same project land directly under
    ``snapshot_root/<conversation_id>/``; the conversation id (a unique uuid) is
    the only grouping key.
    """
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="orchestrator",
        conversation_id="orch-1",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Orchestrator did some work.",
            )
        ],
    )
    _write_run(
        conversation_root,
        agent_name="librarian",
        conversation_id="lib-1",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Librarian did some work.",
            )
        ],
    )

    endpoint = MockLLMEndpoint(
        [{"value": "## State\n- one."}, {"value": "## State\n- two."}]
    )
    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=endpoint,
            agent_names={"orchestrator", "librarian"},
        )
    )(input=Empty(), messages=[])

    assert "created=2" in out.value
    snapshots = sorted(snapshot_root.rglob("*.md"))
    assert len(snapshots) == 2
    # Each snapshot sits directly in snapshot_root/<conversation_id>/: its parent
    # is the conversation id, whose parent is the snapshot root (no agent layer).
    assert {s.parent.name for s in snapshots} == {"orch-1", "lib-1"}
    assert all(s.parent.parent == snapshot_root for s in snapshots)


def test_snapshot_conversations_skips_when_growth_below_threshold(
    tmp_path: Path,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="tiny",
            )
        ],
    )

    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            threshold=10_000,
        )
    )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))
    assert "created=0" in out.value


def test_snapshot_conversations_skips_cancelled_run_even_above_threshold(
    tmp_path: Path,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        status="cancelled",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="User asked to stop immediately.",
            )
        ],
    )

    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            threshold=1,
        )
    )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))
    assert "created=0" in out.value


def test_snapshot_conversations_includes_source_metadata_in_summary_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        conversation_id="conversation-42",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember source metadata.",
            )
        ],
    )
    captured: list[tuple[str, int]] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions
        captured.append((conversation, max_chars))
        return "## User goals\n- Keep metadata."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    conversation, max_chars = captured[0]
    assert "## Source conversation" in conversation
    assert "- Agent: librarian" in conversation
    assert "- Conversation: conversation-42" in conversation
    assert "- Run status: running" in conversation
    assert "- Snapshot mode: incremental" in conversation
    assert "- Run started at: 2026-01-01T00:00:00.000Z" in conversation
    assert "- Run ended at: not ended" in conversation
    assert "- Message ordering: ascending sequence; created_at is UTC context." in (
        conversation
    )
    assert "[sequence=1 created_at=2026-01-01T12:00:00.000Z]" in conversation
    assert max_chars == 10_000


@pytest.mark.parametrize(
    ("status", "expected_mode"),
    [
        (RunStatus.COMPLETED, "terminal-completed"),
        (RunStatus.FAILED, "terminal-failed"),
    ],
)
def test_snapshot_conversations_exposes_terminal_mode_to_prompt(
    tmp_path: Path,
    monkeypatch,
    status: RunStatus,
    expected_mode: str,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        status=status,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Final state evidence.",
            )
        ],
    )
    captured: list[tuple[str, str]] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, max_chars, runtime
        captured.append((instructions, conversation))
        return "## State of work\n- Final state recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    instructions, conversation = captured[0]
    assert instructions == SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert f"- Snapshot mode: {expected_mode}" in conversation


def test_snapshot_conversations_marks_previous_snapshot_as_earlier_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    conversation_id = "conversation-1"
    previous_at = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)
    previous = (
        snapshot_root
        / conversation_id
        / f"{previous_at.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    previous.write_text(
        "# Conversation Snapshot\n\n"
        "## Open loose ends\n- `credibility.md` does not exist.\n"
    )
    _write_run(
        conversation_root,
        conversation_id=conversation_id,
        status=RunStatus.COMPLETED,
        messages=[
            _row(
                sequence=2,
                created_at=previous_at + timedelta(minutes=1),
                content=(
                    "execute_apply_patch_replace: successfully created "
                    "projects/brandstrategy/05-credibility/credibility.md"
                ),
            )
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars, runtime
        captured.append(conversation)
        return (
            "## State of work\n- `credibility.md` exists.\n\n"
            "## Open loose ends\n- None."
        )

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    conversation = captured[0]
    assert (
        "## Previous snapshot (recorded_at=2026-01-01T12:05:00+00:00)" in conversation
    )
    assert "[sequence=2 created_at=2026-01-01T12:06:00.000Z]" in conversation
    assert "successfully created" in conversation


def test_snapshot_conversations_writes_snapshot_for_target_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="librarian",
        conversation_id="child-1",
        parent_conversation_id="parent-1",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember project state metadata.",
            )
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State\n- Snapshot written."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )

    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            agent_names={"librarian"},
        )
    )(input=Empty(), messages=[])

    snapshots = list(snapshot_root.rglob("*.md"))
    assert len(snapshots) == 1
    body = snapshots[0].read_text(encoding="utf-8")
    assert body.startswith("# Conversation Snapshot: librarian")
    assert "Snapshot written." in body
    assert len(captured) == 1
    assert "- Agent: librarian" in captured[0]
    assert "- Conversation: child-1" in captured[0]


def test_snapshot_conversations_propagates_cancellation_without_writing_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cancelled snapshot generation should not write partial snapshot artifacts."""
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="librarian",
        conversation_id="child-1",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember project state metadata.",
            )
        ],
    )

    runtime_pipe = EventPipe()
    runtime_pipe.initialize(dry_run=False, agent_name="librarian")

    def _cancelled_summary(
        *,
        endpoint,
        system_prompt,
        instructions,
        conversation,
        max_chars,
        pipe,
        **runtime,
    ):
        del endpoint, system_prompt, instructions, conversation, max_chars, runtime
        assert pipe is runtime_pipe
        pipe.cancel()
        return "late summary"

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _cancelled_summary
    )

    with pytest.raises(ExternalCallCancelledError):
        snapshot_conversations(
            _ctx(
                conversation_root=conversation_root,
                snapshot_root=snapshot_root,
                endpoint=MockLLMEndpoint([]),
                agent_names={"librarian"},
                pipe=runtime_pipe,
            )
        )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))


def test_snapshot_conversations_normalizes_kind_tagged_bootstrap_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content=json.dumps(
                    {
                        BaseNames.VALUE_FIELD: "# Prior memory\nvery long bootstrap body",
                    }
                ),
                message_kind=MessageKind.STARTUP_CONTEXT,
            ),
            _row(
                sequence=2,
                created_at=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
                content=json.dumps(
                    {
                        BaseNames.VALUE_FIELD: "Banner text should be dropped.",
                    }
                ),
                message_kind=MessageKind.AUTO_LOAD_BANNER,
            ),
            _row(
                sequence=3,
                created_at=datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc),
                content=json.dumps(
                    {
                        BaseNames.CALLER_FIELD: "cli_commands",
                        BaseNames.VALUE_FIELD: "Long skill body that should be omitted.",
                    }
                ),
                message_kind=MessageKind.AUTO_LOADED_SKILL,
            ),
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    conversation = captured[0]
    assert "[startup context omitted: persistent memory injected]" in conversation
    assert "very long bootstrap body" not in conversation
    assert "Banner text should be dropped." not in conversation
    assert "[auto-loaded skill omitted: cli_commands]" in conversation
    assert "Long skill body that should be omitted." not in conversation


def test_snapshot_conversations_compacts_system_prompt_from_role_and_description(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="orchestrator",
        agent_description="Coordinates long-running user goals.",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                role=Role.SYSTEM,
                content="Raw system prompt content",
                message_kind=MessageKind.SYSTEM_MESSAGE,
            ),
            _row(
                sequence=2,
                created_at=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
                content="User-visible task content",
            ),
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            agent_names={"orchestrator"},
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    conversation = captured[0]
    assert (
        "[system prompt omitted] orchestrator: Coordinates long-running user goals."
        in conversation
    )
    assert "Raw system prompt content" not in conversation


def test_snapshot_conversations_system_compaction_has_description_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="librarian",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                role=Role.SYSTEM,
                content="Raw system prompt content",
                message_kind=MessageKind.SYSTEM_MESSAGE,
            )
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    assert (
        "[system prompt omitted] librarian: no persisted agent description"
        in captured[0]
    )


def test_snapshot_conversations_leaves_untagged_json_messages_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    untagged = {
        BaseNames.CALLER_FIELD: "prompt_user",
        BaseNames.VALUE_FIELD: "Here are skills that *I* have chosen to invoke automatically.",
    }
    _write_run(
        conversation_root,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content=json.dumps(untagged),
            )
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    assert json.dumps(untagged) in captured[0]


def test_snapshot_conversations_threshold_uses_normalized_messages(
    tmp_path: Path,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    huge_startup = "# Startup memory\n" + ("bootstrap " * 30_000)
    _write_run(
        conversation_root,
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content=json.dumps(
                    {
                        BaseNames.VALUE_FIELD: huge_startup,
                    }
                ),
                message_kind=MessageKind.STARTUP_CONTEXT,
            )
        ],
    )

    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            threshold=1_000,
        )
    )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))
    assert "created=0" in out.value


def test_snapshot_conversations_skips_agents_outside_allow_list(tmp_path: Path) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        agent_name="librarian",
        messages=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Do not snapshot this agent.",
            )
        ],
    )

    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            agent_names={"orchestrator"},
        )
    )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))
    assert "created=0" in out.value
    assert "skipped_agent=1" in out.value


def test_snapshot_conversations_excludes_messages_the_agent_never_saw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    _write_run(
        conversation_root,
        status="completed",
        messages=[
            _row(
                sequence=1,
                created_at=base,
                content="A startup hook that is removed from live context.",
                truncation=NO_MESSAGE,
            ),
            _row(
                sequence=2,
                created_at=base + timedelta(minutes=1),
                content="A real user instruction the agent actually saw.",
            ),
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    assert "real user instruction" in captured[0]
    assert "startup hook" not in captured[0]


def test_snapshot_conversations_appends_without_deleting_previous_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    conversation_id = "conversation-1"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    previous_time = base + timedelta(minutes=5)
    previous = (
        snapshot_root
        / conversation_id
        / f"{previous_time.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    previous.write_text("# Conversation Snapshot\n\nOld memory about the goal.\n")
    _write_run(
        conversation_root,
        conversation_id=conversation_id,
        status="completed",
        messages=[
            _row(sequence=1, created_at=base, content="Already covered earlier."),
            _row(
                sequence=2,
                created_at=base + timedelta(minutes=10),
                content="New preference to remember for later.",
            ),
        ],
    )
    captured: list[str] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, instructions, max_chars
        captured.append(conversation)
        return "## Preferences and corrections\n- New preference."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    out = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    snapshots = sorted((snapshot_root / conversation_id).glob("*.md"))
    # Append-only: the prior snapshot survives and a new one is added.
    assert len(snapshots) == 2
    assert previous.exists()
    # Only the new segment is summarized, but the previous snapshot is shown as context.
    assert len(captured) == 1
    assert "## Previous snapshot" in captured[0]
    assert "Old memory about the goal" in captured[0]
    assert "New preference to remember" in captured[0]
    assert "Already covered earlier" not in captured[0]
    new_snapshot = next(s for s in snapshots if s != previous)
    assert "New preference" in new_snapshot.read_text()
    assert "created=1" in out.value


def test_snapshot_conversations_prompt_contract_for_overlapping_user_preference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    conversation_id = "conversation-1"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    previous_time = base + timedelta(minutes=5)
    previous = (
        snapshot_root
        / conversation_id
        / f"{previous_time.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    previous.write_text(
        "# Conversation Snapshot\n\n"
        "## Goals and intent\n"
        "- The user is evaluating employment and freelance opportunities.\n"
    )
    preference = (
        "Equity offers are a red flag. Freelance is better than employee work "
        "because personal freedom is paramount."
    )
    patch_payload = {
        "action": "apply_patch",
        "path": "projects/gigs/documents/preferences.md",
        "new_string": (
            "# Job Search Preferences\n\n"
            "- Equity offers = red flag.\n"
            "- Freelance > employee, always.\n"
        ),
    }
    _write_run(
        conversation_root,
        agent_name="orchestrator",
        conversation_id=conversation_id,
        status="completed",
        messages=[
            _row(sequence=1, created_at=base, content="Old setup context."),
            _row(
                sequence=2, created_at=base + timedelta(minutes=10), content=preference
            ),
            _row(
                sequence=3,
                created_at=base + timedelta(minutes=11),
                role=Role.ASSISTANT,
                content=json.dumps(patch_payload),
            ),
        ],
    )
    captured: list[tuple[str, str]] = []

    def _fake_summary(
        *, endpoint, system_prompt, instructions, conversation, max_chars, **runtime
    ):
        del endpoint, system_prompt, max_chars
        captured.append((instructions, conversation))
        return "## Preferences and corrections\n- Equity offers are a red flag."

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", _fake_summary
    )
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            agent_names={"orchestrator"},
        )
    )(input=Empty(), messages=[])

    assert len(captured) == 1
    instructions, conversation = captured[0]
    assert "previous snapshot is context only" in instructions
    assert "must never be used to suppress, deduplicate, or omit information" in (
        instructions
    )
    assert "User-authored messages are paramount evidence" in instructions
    assert "## Previous snapshot" in conversation
    assert "evaluating employment and freelance opportunities" in conversation
    assert "## New conversation segment" in conversation
    assert preference in conversation
    assert "projects/gigs/documents/preferences.md" in conversation
    assert "Freelance > employee, always" in conversation
    assert "Old setup context" not in conversation


def test_snapshot_prompt_keeps_markdown_inside_structured_value() -> None:
    assert "structured response's `value` field" in (
        snapshot_module.SNAPSHOT_CONVERSATION_SYSTEM_PROMPT
    )
    assert "Output plain markdown only" not in (
        snapshot_module.SNAPSHOT_CONVERSATION_SYSTEM_PROMPT
    )
