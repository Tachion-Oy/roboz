"""Regression tests for deterministic Librarian composition."""

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import pytest
from roboshed.agents.librarian import (
    LIBRARIAN_AGENT_DESCRIPTION,
    LibrarianTuning,
    librarian,
)
from roboshed.tools.librarian_errors import LibrarianProviderRequestFailure
from roboshed.workspace import Project, Workspace

from roboz.exceptions import ExternalCallCancelledError, LLMAuthError
from roboz.llm import MockLLMEndpoint, MockProviderError
from roboz.models import Empty, Message, Role, Stop, Str
from roboz.runtime import default_event_sinks
from roboz.runtime.persistence import (
    ConversationRun,
    RunStatus,
    clear_conversation_active,
    mark_conversation_active,
    message_to_logged_row,
    utc_iso_z,
)

sleep_module = importlib.import_module("roboshed.tools.sleep_between_runs")

_WATCHED_AGENT: Final[str] = "orchestrator"


def _paths(tmp_path: Path) -> Project:
    return Project(Workspace(tmp_path), "test")


def _build(
    tmp_path: Path,
    *,
    endpoint: MockLLMEndpoint | None = None,
    tuning: LibrarianTuning | None = None,
):
    return librarian(
        project=_paths(tmp_path),
        agent_names={_WATCHED_AGENT},
        snapshot_endpoint=endpoint or MockLLMEndpoint([]),
        tuning=tuning
        or LibrarianTuning(
            token_growth_threshold=100,
            min_pending_snapshots=3,
            max_pending_age_seconds=3_600.0,
            sleep_seconds=0.01,
        ),
    ).build(
        event_sink_factory=lambda name: default_event_sinks(
            data_path=_paths(tmp_path).logs / name, include_cli=False
        )
    )


def _write_source_run(
    paths: Project, *, status: RunStatus = RunStatus.COMPLETED
) -> Path:
    created_at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    run = ConversationRun(
        conversation_id="source-run",
        agent_name=_WATCHED_AGENT,
        started_at=utc_iso_z(created_at),
        ended_at=(None if status is RunStatus.RUNNING else utc_iso_z(created_at)),
        status=status,
        messages=[
            message_to_logged_row(
                Message(role=Role.USER, content="Persist this source state."),
                message_id="source-message",
                sequence=1,
                created_at=created_at,
            )
        ],
    )
    agent_dir = paths.logs / _WATCHED_AGENT
    path = agent_dir / "source-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(), encoding="utf-8")
    if status is RunStatus.RUNNING:
        mark_conversation_active(
            agent_dir=agent_dir, conversation_id=run.conversation_id
        )
    return path


def test_librarian_wires_exact_ordered_maintenance_pipeline(tmp_path: Path) -> None:
    agent = _build(tmp_path)

    assert [tool.name for tool in agent.default_tools] == [
        "snapshot_conversations",
        "consolidate_memory",
        "purge_logs",
        "purge_snapshots",
        "purge_memory",
        "sleep_between_runs",
    ]
    assert agent.agent_endpoint is None
    assert agent.description == LIBRARIAN_AGENT_DESCRIPTION


def test_librarian_tuning_has_audited_defaults_and_validation() -> None:
    tuning = LibrarianTuning()
    assert tuning.sleep_seconds == 120
    assert tuning.token_growth_threshold == 20_000
    assert tuning.max_chars_tolerance_percent == 15.0
    assert tuning.llm_timeout_s == 300.0

    with pytest.raises(ValueError, match="llm_timeout_s"):
        LibrarianTuning(llm_timeout_s=0)
    for invalid in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_chars_tolerance_percent"):
            LibrarianTuning(max_chars_tolerance_percent=invalid)


def test_librarian_requires_exactly_one_endpoint_source(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        librarian(project=_paths(tmp_path), agent_names={_WATCHED_AGENT})
    with pytest.raises(ValueError, match="exactly one"):
        librarian(
            project=_paths(tmp_path),
            agent_names={_WATCHED_AGENT},
            snapshot_endpoint=MockLLMEndpoint([]),
            endpoint_factory=lambda is_cancelled: MockLLMEndpoint([]),
        )


def test_endpoint_factory_receives_bound_cancellation_probe(tmp_path: Path) -> None:
    observed: list = []

    def endpoint_factory(is_cancelled):
        observed.append(is_cancelled)
        return MockLLMEndpoint([])

    definition = librarian(
        project=_paths(tmp_path),
        agent_names={_WATCHED_AGENT},
        endpoint_factory=endpoint_factory,
    )
    agent = definition.build()
    (is_cancelled,) = observed
    assert is_cancelled() is False

    agent.pipe.cancel()

    assert is_cancelled() is True


def test_snapshot_retention_prunes_empty_conversation_folders(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    artifact = paths.snapshots / "retained" / "one.md"
    empty = paths.snapshots / "empty-conversation"
    artifact.parent.mkdir(parents=True)
    empty.mkdir(parents=True)
    artifact.write_text("snapshot", encoding="utf-8")
    agent = _build(tmp_path)
    purge = next(tool for tool in agent.default_tools if tool.name == "purge_snapshots")

    result = purge(input=Empty(), messages=[])

    assert "pruned_empty_dirs=1" in result.value
    assert artifact.is_file()
    assert not empty.exists()


def test_wait_stops_immediately_when_project_is_idle(tmp_path: Path) -> None:
    agent = _build(tmp_path)
    wait = next(
        tool for tool in agent.default_tools if tool.name == "sleep_between_runs"
    )

    result = wait(input=Empty(), messages=[])

    assert isinstance(result, Stop)
    assert result.value == "sleep_between_runs: project idle"


def test_wait_returns_when_observed_active_run_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    source = _write_source_run(paths, status=RunStatus.RUNNING)
    agent = _build(tmp_path, tuning=LibrarianTuning(sleep_seconds=120.0))
    wait = next(
        tool for tool in agent.default_tools if tool.name == "sleep_between_runs"
    )

    def finish_run(seconds: float) -> None:
        del seconds
        run = ConversationRun.model_validate_json(source.read_text(encoding="utf-8"))
        clear_conversation_active(
            agent_dir=source.parent, conversation_id=run.conversation_id
        )

    monkeypatch.setattr(sleep_module, "sleep", finish_run)
    result = wait(input=Empty(), messages=[])

    assert isinstance(result, Str)
    assert result.value == "sleep_between_runs: active run ended"


def test_wait_observes_agent_pipe_cancellation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_source_run(paths, status=RunStatus.RUNNING)
    agent = _build(tmp_path, tuning=LibrarianTuning(sleep_seconds=120.0))
    wait = next(
        tool for tool in agent.default_tools if tool.name == "sleep_between_runs"
    )
    agent.pipe.cancel()

    with pytest.raises(ExternalCallCancelledError):
        wait(input=Empty(), messages=[])


def test_librarian_invoke_persists_no_message_cycle_outputs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    agent = _build(tmp_path)

    agent.invoke()

    run_files = list((paths.logs / "librarian").rglob("*.json"))
    assert len(run_files) == 1
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run["agent_name"] == "librarian"
    joined = "\n".join(message["content"] for message in run["messages"])
    assert "snapshot_conversations" in joined
    assert "consolidate_memory" in joined
    assert "sleep_between_runs" in joined
    assert len(run["messages"]) > 1
    assert run["runtime_events"]


def test_provider_failure_is_sanitized_in_persisted_failed_run(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_source_run(paths)
    secret = "secret-value-123456789"
    endpoint = MockLLMEndpoint(
        [MockProviderError(f"Invalid api_key={secret}", status_code=401)]
    )
    agent = _build(
        tmp_path,
        endpoint=endpoint,
        tuning=LibrarianTuning(token_growth_threshold=1, sleep_seconds=0.01),
    )

    with pytest.raises(LibrarianProviderRequestFailure) as raised:
        agent.invoke()

    assert isinstance(raised.value.__cause__, LLMAuthError)
    run_files = list((paths.logs / "librarian").rglob("*.json"))
    assert len(run_files) == 1
    run_text = run_files[0].read_text(encoding="utf-8")
    run = json.loads(run_text)
    assert run["status"] == "failed"
    assert secret not in run_text
    failure = next(
        event
        for event in run["runtime_events"]
        if event["category"] == "tool" and event["kind"] == "failed"
    )
    assert failure["data"]["tool"] == "snapshot_conversations"


def test_builtin_default_configuration_is_preserved() -> None:
    from roboshed.tools import sleep_between_runs

    import roboz as rz

    result = sleep_between_runs(rz.Ctx(seconds=0))(rz.All(), [])
    assert isinstance(result, rz.Str)
    assert result.value == "sleep_between_runs: slept=0.0s"
