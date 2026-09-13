"""Regression tests for storage-backed conversation snapshots."""

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from roboshed.tools import SnapshotConversationsContext
from roboshed.tools import snapshot_conversations
from roboshed.tools._snapshot_metadata import (
    SNAPSHOT_COVERAGE_SEQUENCE_FIELD,
    SNAPSHOT_COVERAGE_TAG,
    SNAPSHOT_COVERAGE_VERSION,
    parse_snapshot_document,
)
from roboshed.tools.librarian_errors import LibrarianProviderRequestFailure
from roboshed.tools.memory_files import TIMESTAMP_STEM_FORMAT

from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.llm import MockLLMEndpoint
from roboz.models import DEFAULT, BaseNames, Empty, Message, MessageKind, Role
from roboz.runtime import LOG_DATA_ATTRIBUTE, EventPipe
from roboz.runtime.persistence import (
    ConversationRun,
    LoggedMessageRow,
    RunMetadata,
    RunStatus,
    message_to_logged_row,
    utc_iso_z,
)

snapshot_module = importlib.import_module("roboshed.tools.snapshot_conversations")

_DEFAULT_AGENT = "code_task_executor"
_DEFAULT_CONVERSATION = "conversation-1"
_MAX_CHARS = 10_000


def _row(
    *,
    sequence: int,
    created_at: datetime,
    content: str,
    role: Role = Role.USER,
    message_kind: MessageKind | None = None,
):
    return message_to_logged_row(
        Message(
            role=role,
            content=content,
            truncation=DEFAULT,
            message_kind=message_kind,
        ),
        message_id=f"m-{sequence}",
        sequence=sequence,
        created_at=created_at,
    )


def _write_run(
    root: Path,
    *,
    agent_name: str = _DEFAULT_AGENT,
    conversation_id: str = _DEFAULT_CONVERSATION,
    status: RunStatus = RunStatus.RUNNING,
    agent_description: str | None = None,
    rows: list[LoggedMessageRow] | None = None,
) -> Path:
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_name=agent_name,
        started_at=utc_iso_z(started_at),
        ended_at=None if status is RunStatus.RUNNING else utc_iso_z(started_at),
        status=status,
        metadata=RunMetadata(agent_description=agent_description),
        messages=[] if rows is None else rows,
    )
    path = root / agent_name / f"{conversation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(), encoding="utf-8")
    return path


def _ctx(
    *,
    conversation_root: Path,
    snapshot_root: Path,
    endpoint: MockLLMEndpoint,
    agent_names: set[str] | None = None,
    threshold: int = 1,
    pipe: EventPipe | None = None,
) -> SnapshotConversationsContext:
    return SnapshotConversationsContext(
        endpoint=endpoint,
        conversation_root=conversation_root,
        snapshot_root=snapshot_root,
        memory_root=snapshot_root.parent / "memory",
        agent_names=agent_names or {_DEFAULT_AGENT},
        token_growth_threshold=threshold,
        max_chars=_MAX_CHARS,
        pipe=pipe,
    )


def test_snapshot_writes_append_only_artifact_with_structured_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember the work.",
            )
        ],
    )
    endpoint = MockLLMEndpoint([{"value": "## State of work\n- Recorded."}])

    with caplog.at_level("INFO"):
        result = snapshot_conversations(
            _ctx(
                conversation_root=conversation_root,
                snapshot_root=snapshot_root,
                endpoint=endpoint,
            )
        )(input=Empty(), messages=[])

    artifacts = list(snapshot_root.rglob("*.md"))
    assert len(artifacts) == 1
    assert artifacts[0].parent == snapshot_root / _DEFAULT_CONVERSATION
    snapshot_document = parse_snapshot_document(
        artifacts[0].read_text(encoding="utf-8")
    )
    assert snapshot_document.content.startswith(
        f"# Conversation Snapshot: {_DEFAULT_AGENT}"
    )
    assert snapshot_document.covered_through_sequence == 1
    assert "created=1" in result.value
    record = next(
        item
        for item in caplog.records
        if item.getMessage().startswith("Librarian snapshot created")
    )
    assert getattr(record, LOG_DATA_ATTRIBUTE)["conversation_id"] == (
        _DEFAULT_CONVERSATION
    )


@pytest.mark.parametrize(
    ("status", "expected_mode"),
    [
        (RunStatus.COMPLETED, "terminal-completed"),
        (RunStatus.FAILED, "terminal-failed"),
    ],
)
def test_terminal_run_flushes_below_threshold_and_exposes_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
    expected_mode: str,
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        status=status,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Final evidence.",
            )
        ],
    )
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## State of work\n- Final state."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    result = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            threshold=100_000,
        )
    )(input=Empty(), messages=[])

    assert "created=1" in result.value
    assert f"- Snapshot mode: {expected_mode}" in captured[0]


def test_cancelled_run_is_never_snapshotted(tmp_path: Path) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        status=RunStatus.CANCELLED,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Do not preserve this cancelled work.",
            )
        ],
    )

    result = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "created=0" in result.value
    assert not list(snapshot_root.rglob("*.md"))


def test_snapshot_input_has_source_metadata_and_chronological_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        conversation_id="conversation-42",
        rows=[
            _row(
                sequence=7,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember source metadata.",
            )
        ],
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
        return "## State of work\n- Recorded."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    conversation, max_chars, tolerance = captured[0]
    assert "- Agent: code_task_executor" in conversation
    assert "- Conversation: conversation-42" in conversation
    assert "- Run status: running" in conversation
    assert "- Snapshot mode: incremental" in conversation
    assert "[sequence=7 created_at=2026-01-01T12:00:00.000Z]" in conversation
    assert max_chars == _MAX_CHARS
    assert tolerance == 15.0


def test_previous_snapshot_is_grounding_and_only_uncovered_rows_are_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    previous_time = base + timedelta(minutes=5)
    previous = (
        snapshot_root
        / _DEFAULT_CONVERSATION
        / f"{previous_time.strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    previous.write_text("## Open loose ends\n- File is absent.\n", encoding="utf-8")
    _write_run(
        conversation_root,
        status=RunStatus.COMPLETED,
        rows=[
            _row(sequence=1, created_at=base, content="Already covered."),
            _row(
                sequence=2,
                created_at=base + timedelta(minutes=10),
                content="The file was created successfully.",
            ),
        ],
    )
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## State of work\n- File exists."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "## Previous snapshot (recorded_at=" in captured[0]
    assert "File is absent" in captured[0]
    assert "file was created successfully" in captured[0]
    assert "Already covered" not in captured[0]
    assert previous.exists()
    assert len(list(previous.parent.glob("*.md"))) == 2


def test_rows_appended_during_snapshot_are_covered_by_the_next_sequence_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    created_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    first_row = _row(
        sequence=1,
        created_at=created_at,
        content="Visible to the first snapshot.",
    )
    appended_row = _row(
        sequence=2,
        created_at=created_at,
        content="Appended while the first snapshot is generated.",
    )
    _write_run(conversation_root, rows=[first_row])
    captured: list[str] = []

    def append_during_first_summary(**kwargs):
        captured.append(kwargs["conversation"])
        if len(captured) == 1:
            _write_run(conversation_root, rows=[first_row, appended_row])
        return "## State of work\n- Recorded."

    monkeypatch.setattr(
        snapshot_module,
        "summarize_conversation_segment",
        append_during_first_summary,
    )
    context = _ctx(
        conversation_root=conversation_root,
        snapshot_root=snapshot_root,
        endpoint=MockLLMEndpoint([]),
    )

    first_result = snapshot_conversations(context)(input=Empty(), messages=[])
    first_snapshot = max(snapshot_root.rglob("*.md"))
    first_document = parse_snapshot_document(first_snapshot.read_text(encoding="utf-8"))
    second_result = snapshot_conversations(context)(input=Empty(), messages=[])
    snapshots = sorted(snapshot_root.rglob("*.md"))
    latest_document = parse_snapshot_document(snapshots[-1].read_text(encoding="utf-8"))

    assert "created=1" in first_result.value
    assert first_document.covered_through_sequence == 1
    assert "Visible to the first snapshot" in captured[0]
    assert "Appended while the first snapshot" not in captured[0]
    assert "created=1" in second_result.value
    assert len(snapshots) == 2
    assert "Appended while the first snapshot" in captured[1]
    assert "Visible to the first snapshot" not in captured[1]
    assert SNAPSHOT_COVERAGE_TAG not in captured[1]
    assert latest_document.covered_through_sequence == 2


def test_legacy_snapshot_treats_naive_row_timestamp_as_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    previous = (
        snapshot_root
        / _DEFAULT_CONVERSATION
        / f"{(base + timedelta(minutes=5)).strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    previous.write_text("Legacy snapshot.", encoding="utf-8")
    naive_row = _row(
        sequence=1,
        created_at=base,
        content="Conservatively reprocess this row.",
    ).model_copy(update={"created_at": "2026-01-01T12:00:00"})
    _write_run(conversation_root, rows=[naive_row])
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## State of work\n- Reprocessed."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    result = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "created=1" in result.value
    assert "Conservatively reprocess this row" in captured[0]


def test_malformed_coverage_marker_reprocesses_rows_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    previous = (
        snapshot_root
        / _DEFAULT_CONVERSATION
        / f"{(base + timedelta(minutes=5)).strftime(TIMESTAMP_STEM_FORMAT)}.md"
    )
    previous.parent.mkdir(parents=True)
    malformed_marker = (
        f"<!-- {SNAPSHOT_COVERAGE_TAG} v{SNAPSHOT_COVERAGE_VERSION} "
        f"{SNAPSHOT_COVERAGE_SEQUENCE_FIELD}=invalid -->"
    )
    previous.write_text(
        f"Legacy snapshot.\n\n{malformed_marker}\n",
        encoding="utf-8",
    )
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=base,
                content="Do not silently skip malformed coverage.",
            )
        ],
    )
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## State of work\n- Reprocessed."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    assert "Do not silently skip malformed coverage" in captured[0]
    assert SNAPSHOT_COVERAGE_TAG not in captured[0]


def test_bootstrap_payloads_are_normalized_before_thresholding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    rows = [
        _row(
            sequence=1,
            created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            role=Role.SYSTEM,
            content="raw system prompt",
            message_kind=MessageKind.SYSTEM_MESSAGE,
        ),
        _row(
            sequence=2,
            created_at=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
            content=json.dumps({BaseNames.VALUE_FIELD: "huge " * 30_000}),
            message_kind=MessageKind.STARTUP_CONTEXT,
        ),
        _row(
            sequence=3,
            created_at=datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc),
            content=json.dumps(
                {
                    BaseNames.CALLER_FIELD: "cli_tools",
                    BaseNames.VALUE_FIELD: "skill body",
                }
            ),
            message_kind=MessageKind.AUTO_LOADED_SKILL,
        ),
        _row(
            sequence=4,
            created_at=datetime(2026, 1, 1, 12, 3, tzinfo=timezone.utc),
            content="banner body",
            message_kind=MessageKind.AUTO_LOAD_BANNER,
        ),
    ]
    _write_run(
        conversation_root,
        agent_description="Coordinates work.",
        rows=rows,
    )
    captured: list[str] = []

    def fake_summary(**kwargs):
        captured.append(kwargs["conversation"])
        return "## State of work\n- Recorded."

    monkeypatch.setattr(snapshot_module, "summarize_conversation_segment", fake_summary)
    snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
            threshold=1,
        )
    )(input=Empty(), messages=[])

    conversation = captured[0]
    assert (
        "[system prompt omitted] code_task_executor: Coordinates work." in conversation
    )
    assert "[startup context omitted: persistent memory injected]" in conversation
    assert "[auto-loaded skill omitted: cli_tools]" in conversation
    assert "raw system prompt" not in conversation
    assert "skill body" not in conversation
    assert "banner body" not in conversation


def test_snapshot_rechecks_cancellation_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Work.",
            )
        ],
    )
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="librarian")

    def cancel_summary(**kwargs):
        assert kwargs["pipe"] is pipe
        pipe.cancel()
        return "late summary"

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", cancel_summary
    )
    with pytest.raises(ExternalCallCancelledError):
        snapshot_conversations(
            _ctx(
                conversation_root=conversation_root,
                snapshot_root=snapshot_root,
                endpoint=MockLLMEndpoint([]),
                pipe=pipe,
            )
        )(input=Empty(), messages=[])

    assert not list(snapshot_root.rglob("*.md"))


def test_snapshot_skips_redundant_twin_when_watermark_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Work.",
            )
        ],
    )

    def racing_summary(**kwargs):
        del kwargs
        competing = (
            snapshot_root
            / _DEFAULT_CONVERSATION
            / f"{datetime.now(timezone.utc).strftime(TIMESTAMP_STEM_FORMAT)}.md"
        )
        competing.parent.mkdir(parents=True, exist_ok=True)
        competing.write_text("competing snapshot", encoding="utf-8")
        return "redundant snapshot"

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", racing_summary
    )
    result = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([]),
        )
    )(input=Empty(), messages=[])

    artifacts = list(snapshot_root.rglob("*.md"))
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8") == "competing snapshot"
    assert "created=0" in result.value


def test_snapshot_translates_provider_request_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Work.",
            )
        ],
    )

    def failing_summary(**kwargs):
        del kwargs
        raise LLMProviderRequestError("bad request")

    monkeypatch.setattr(
        snapshot_module, "summarize_conversation_segment", failing_summary
    )
    with pytest.raises(LibrarianProviderRequestFailure):
        snapshot_conversations(
            _ctx(
                conversation_root=conversation_root,
                snapshot_root=snapshot_root,
                endpoint=MockLLMEndpoint([]),
            )
        )(input=Empty(), messages=[])


@pytest.mark.parametrize("invalid_content", [b"not json", b'{"content": "\xff"}'])
def test_snapshot_counts_invalid_and_unwatched_runs(
    tmp_path: Path, invalid_content: bytes
) -> None:
    conversation_root = tmp_path / "runs"
    snapshot_root = tmp_path / "snapshots"
    invalid = conversation_root / "broken.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(invalid_content)
    _write_run(
        conversation_root,
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Remember the valid neighboring conversation.",
            )
        ],
    )
    _write_run(
        conversation_root,
        agent_name="unwatched",
        rows=[
            _row(
                sequence=1,
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                content="Ignore.",
            )
        ],
    )

    result = snapshot_conversations(
        _ctx(
            conversation_root=conversation_root,
            snapshot_root=snapshot_root,
            endpoint=MockLLMEndpoint([{"value": "Valid conversation summary."}]),
        )
    )(input=Empty(), messages=[])

    assert "scanned=3" in result.value
    assert "skipped_invalid=1" in result.value
    assert "skipped_agent=1" in result.value
    assert "created=1" in result.value
    artifacts = list(snapshot_root.rglob("*.md"))
    assert len(artifacts) == 1
    assert artifacts[0].parent == snapshot_root / _DEFAULT_CONVERSATION
    assert "Valid conversation summary." in artifacts[0].read_text(encoding="utf-8")
    assert invalid.read_bytes() == invalid_content
