"""Regression tests for deterministic Librarian composition."""

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Final

import pytest
from roboshed.agents.librarian import LIBRARIAN_AGENT_DESCRIPTION
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.tools.contexts import SleepBetweenRunsContext
from roboshed.tools.librarian_errors import LibrarianProviderRequestFailure
from roboshed.sandbox import Sandbox

from roboz.agent import AgentMode
from roboz.exceptions import ExternalCallCancelledError, LLMAuthError
from roboz.deployment import DeployableAgent
from roboz.llm import MockLLMEndpoint, MockProviderError
from roboz.models import Empty, Message, Role, Stop, Str
from roboz.models._serialization import get_finalized_message
from roboz.runtime import PipeEvent, RuntimeEvent, default_event_sinks
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


def _sandbox(tmp_path: Path) -> Sandbox:
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("test")
    return sandbox


def _build(
    tmp_path: Path,
    *,
    endpoint: MockLLMEndpoint | None = None,
    token_growth_threshold: int = 100,
    sleep_seconds: float = 0.01,
    watched_agent_names: set[str] | None = None,
):
    sandbox = _sandbox(tmp_path)
    names = {_WATCHED_AGENT} if watched_agent_names is None else watched_agent_names
    definition = _librarian(
        sandbox,
        names,
        endpoint=endpoint or MockLLMEndpoint([]),
        capabilities=(
            ConversationSnapshots(token_growth_threshold=token_growth_threshold),
            MemoryConsolidation(
                min_pending_snapshots=3,
                max_pending_age_seconds=3_600,
            ),
            ArtifactRetention(),
            MaintenanceCadence(seconds=sleep_seconds),
        ),
    )
    return definition.build(
        event_sink_factory=lambda name: default_event_sinks(
            data_path=sandbox.project_logs_dir() / name, include_cli=False
        )
    )[0]


def _librarian(sandbox, names, *, endpoint, capabilities):
    definition = DeployableAgent(
        name="librarian",
        description=LIBRARIAN_AGENT_DESCRIPTION,
        mode=AgentMode.DETERMINISTIC,
        automatic_tool_prompt=False,
        default_capabilities=capabilities,
    )
    definition.set_agent_endpoint(endpoint)
    definition.set_attributes(
        sandbox=sandbox,
        watched_agent_names=names,
    )
    return definition


def _write_source_run(
    sandbox: Sandbox,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    agent_name: str = _WATCHED_AGENT,
    conversation_id: str = "source-run",
) -> Path:
    created_at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_name=agent_name,
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
    agent_dir = sandbox.project_logs_dir() / agent_name
    path = agent_dir / f"{conversation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(), encoding="utf-8")
    if status is RunStatus.RUNNING:
        mark_conversation_active(
            agent_dir=agent_dir, conversation_id=run.conversation_id
        )
    return path


def _complete_source_run(source: Path, *, final_fact: str = "The final fact.") -> None:
    run = ConversationRun.model_validate_json(source.read_text(encoding="utf-8"))
    if run.status is RunStatus.COMPLETED:
        return
    run.status = RunStatus.COMPLETED
    run.ended_at = utc_iso_z(datetime(2026, 8, 4, tzinfo=timezone.utc))
    run.messages.append(
        message_to_logged_row(
            Message(role=Role.ASSISTANT, content=final_fact),
            message_id=f"{run.conversation_id}-final-message",
            sequence=max(row.sequence for row in run.messages) + 1,
            created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    )
    source.write_text(run.model_dump_json(), encoding="utf-8")
    clear_conversation_active(
        agent_dir=source.parent, conversation_id=run.conversation_id
    )


def _tool_callers(messages: list[Message]) -> list[str]:
    return [
        caller
        for message in messages
        if (caller := json.loads(message.content).get("caller")) is not None
    ]


def test_librarian_wires_exact_ordered_maintenance_pipeline(tmp_path: Path) -> None:
    agent = _build(tmp_path)

    assert [tool.name for tool in agent.default_tools] == [
        "snapshot_conversations",
        "consolidate_memory",
        "purge_logs",
        "purge_snapshots",
        "purge_memory",
        "stop_when_watched_agents_inactive",
        "sleep_between_runs",
    ]
    assert agent.mode is AgentMode.DETERMINISTIC
    assert isinstance(agent.agent_endpoint, MockLLMEndpoint)
    assert agent.description == LIBRARIAN_AGENT_DESCRIPTION


@pytest.mark.parametrize(
    "capability_type", [ConversationSnapshots, MemoryConsolidation]
)
def test_summary_capabilities_have_audited_defaults_and_validation(capability_type):
    capability = capability_type()
    assert capability.max_chars_tolerance_percent == 15.0
    assert capability.timeout_s == 300.0

    with pytest.raises(ValueError, match="timeout_s"):
        capability_type(timeout_s=0)
    for invalid in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_chars_tolerance_percent"):
            capability_type(
                max_chars_tolerance_percent=invalid,
            )


def test_maintenance_capability_defaults():
    snapshots = ConversationSnapshots()
    consolidation = MemoryConsolidation()
    retention = ArtifactRetention()
    assert snapshots.token_growth_threshold == 20_000
    assert snapshots.max_chars == 8_000
    assert consolidation.max_chars == 12_000
    assert consolidation.min_pending_snapshots == 3
    assert consolidation.max_pending_age_seconds == 86_400
    assert (
        retention.max_log_files,
        retention.max_snapshot_files,
        retention.max_memory_files,
    ) == (500, 100, 10)
    assert MaintenanceCadence().seconds == 120


@pytest.mark.parametrize(
    "capability_type", [ConversationSnapshots, MemoryConsolidation]
)
def test_each_summary_capability_requires_a_model_on_build(tmp_path, capability_type):
    sandbox = _sandbox(tmp_path)
    definition = _librarian(
        sandbox,
        {_WATCHED_AGENT},
        endpoint=None,
        capabilities=(capability_type(),),
    )
    with pytest.raises(ValueError, match="agent_endpoint.*None"):
        definition.build()


def test_librarian_accepts_selected_capabilities_without_a_model(tmp_path):
    sandbox = _sandbox(tmp_path)
    definition = _librarian(
        sandbox,
        {_WATCHED_AGENT},
        endpoint=None,
        capabilities=(ArtifactRetention(), MaintenanceCadence()),
    )
    agent, _ = definition.build()
    assert [tool.name for tool in agent.default_tools] == [
        "purge_logs",
        "purge_snapshots",
        "purge_memory",
        "stop_when_watched_agents_inactive",
        "sleep_between_runs",
    ]
    result, _ = agent.invoke()
    assert "watched agents inactive" in result.value
    assert agent.external_dependencies() == ()


def test_librarians_share_endpoint_but_have_independent_cancellation(
    tmp_path: Path,
) -> None:
    endpoint = MockLLMEndpoint([])
    sandbox = _sandbox(tmp_path)
    definition = _librarian(
        sandbox,
        {_WATCHED_AGENT},
        endpoint=endpoint,
        capabilities=(ConversationSnapshots(), MaintenanceCadence()),
    )
    first = definition.build()[0]
    second = definition.build()[0]
    first.pipe.cancel()

    assert first.agent_endpoint is second.agent_endpoint is endpoint
    with pytest.raises(ExternalCallCancelledError):
        first.default_tools[0](input=Empty(), messages=[])
    result, _ = second.invoke()
    assert "watched agents inactive" in result.value
    assert not second.pipe.cancelled


def test_snapshot_retention_prunes_empty_conversation_folders(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    snapshots = sandbox.project_snapshots_dir()
    artifact = snapshots / "retained" / "one.md"
    empty = snapshots / "empty-conversation"
    artifact.parent.mkdir(parents=True)
    empty.mkdir(parents=True)
    artifact.write_text("snapshot", encoding="utf-8")
    agent = _build(tmp_path)
    purge = next(tool for tool in agent.default_tools if tool.name == "purge_snapshots")

    result = purge(input=Empty(), messages=[])

    assert "pruned_empty_dirs=1" in result.value
    assert artifact.is_file()
    assert not empty.exists()


def test_idle_project_gets_a_complete_final_sweep_before_stopping(
    tmp_path: Path,
) -> None:
    agent = _build(tmp_path)

    result, messages = agent.invoke()

    assert isinstance(result, Stop)
    assert result.value == "stop_when_watched_agents_inactive: watched agents inactive"
    calls = _tool_callers(messages)
    assert calls.count("snapshot_conversations") == 2
    assert calls.count("consolidate_memory") == 2
    assert calls.count("purge_memory") == 2
    assert calls.count("sleep_between_runs") == 1


def test_wait_returns_when_observed_active_run_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _sandbox(tmp_path)
    source = _write_source_run(sandbox, status=RunStatus.RUNNING)
    agent = _build(tmp_path, sleep_seconds=120.0)
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


@pytest.mark.parametrize(
    "complete_after",
    [
        "snapshot_conversations",
        "consolidate_memory",
        "purge_memory",
        "stop_when_watched_agents_inactive",
        "sleep_between_runs",
    ],
)
def test_completion_during_maintenance_is_flushed_without_sleeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete_after: str
) -> None:
    sandbox = _sandbox(tmp_path)
    source = _write_source_run(sandbox, status=RunStatus.RUNNING)
    endpoint = MockLLMEndpoint(
        [
            {"value": "Final fact from the completed run."},
            {"value": "Memory includes the final fact."},
        ]
    )
    agent = _build(
        tmp_path,
        endpoint=endpoint,
        token_growth_threshold=100_000,
        sleep_seconds=120.0,
    )

    def complete_after_tool(event: PipeEvent) -> None:
        if not isinstance(event, RuntimeEvent):
            return
        if event.category != "tool" or event.kind != "succeeded":
            return
        if event.data is None or event.data.get("tool") != complete_after:
            return
        _complete_source_run(source)

    if complete_after == "sleep_between_runs":
        monkeypatch.setattr(
            sleep_module, "sleep", lambda _seconds: _complete_source_run(source)
        )
    else:
        agent.pipe.add_sink(complete_after_tool)
        monkeypatch.setattr(
            sleep_module,
            "sleep",
            lambda seconds: pytest.fail(f"final sweep slept for {seconds}s"),
        )

    result, _ = agent.invoke()

    assert result.value == "stop_when_watched_agents_inactive: watched agents inactive"
    snapshots = list(sandbox.project_snapshots_dir().rglob("*.md"))
    memories = list(sandbox.project_memory_dir().glob("*.md"))
    assert len(snapshots) == 1
    assert len(memories) == 1
    assert "Final fact from the completed run" in snapshots[0].read_text()
    assert "Memory includes the final fact" in memories[0].read_text()


def test_all_watched_workers_finish_before_final_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _sandbox(tmp_path)
    first = _write_source_run(
        sandbox,
        status=RunStatus.RUNNING,
        agent_name="worker-one",
        conversation_id="first-run",
    )
    second = _write_source_run(
        sandbox,
        status=RunStatus.RUNNING,
        agent_name="worker-two",
        conversation_id="second-run",
    )
    agent = _build(
        tmp_path,
        endpoint=MockLLMEndpoint(
            [
                {"value": "First worker final fact."},
                {"value": "Second worker final fact."},
                {"value": "Memory includes both worker facts."},
            ]
        ),
        token_growth_threshold=100_000,
        sleep_seconds=120,
        watched_agent_names={"worker-one", "worker-two"},
    )

    def complete_first_after_snapshot(event: PipeEvent) -> None:
        if (
            isinstance(event, RuntimeEvent)
            and event.category == "tool"
            and event.kind == "succeeded"
            and event.data is not None
            and event.data.get("tool") == "snapshot_conversations"
        ):
            _complete_source_run(first, final_fact="First worker fact.")

    agent.pipe.add_sink(complete_first_after_snapshot)
    monkeypatch.setattr(
        sleep_module,
        "sleep",
        lambda _seconds: _complete_source_run(
            second, final_fact="Second worker fact."
        ),
    )

    result, _ = agent.invoke()

    assert result.value == (
        "stop_when_watched_agents_inactive: watched agents inactive"
    )
    snapshots = list(sandbox.project_snapshots_dir().rglob("*.md"))
    memories = list(sandbox.project_memory_dir().glob("*.md"))
    assert len(snapshots) == 2
    assert len(memories) == 1
    assert {path.parent.name for path in snapshots} == {"first-run", "second-run"}
    assert "both worker facts" in memories[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run", [False, True])
def test_repeated_idle_invocations_each_get_a_final_sweep(
    tmp_path: Path, dry_run: bool
) -> None:
    agent = _build(tmp_path)

    for _ in range(2):
        result, messages = agent.invoke(dry_run=dry_run)
        assert (
            result.value == "stop_when_watched_agents_inactive: watched agents inactive"
        )
        calls = _tool_callers(messages)
        assert calls.count("snapshot_conversations") == 2


def test_activity_requires_a_new_final_sweep(tmp_path: Path) -> None:
    agent = _build(tmp_path)
    check = next(
        tool
        for tool in agent.default_tools
        if tool.name == "stop_when_watched_agents_inactive"
    )
    messages: list[Message] = []

    def check_idle() -> Str | Stop:
        result = check(Empty(), messages)
        messages.append(get_finalized_message(result, check))
        return result

    assert isinstance(check_idle(), Str)
    source = _write_source_run(_sandbox(tmp_path), status=RunStatus.RUNNING)
    assert (
        check_idle().value == "stop_when_watched_agents_inactive: watched agents active"
    )
    clear_conversation_active(agent_dir=source.parent, conversation_id="source-run")

    assert (
        check_idle().value == "stop_when_watched_agents_inactive: final sweep required"
    )
    assert isinstance(check_idle(), Stop)


def test_wait_observes_cancellation_during_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_source_run(_sandbox(tmp_path), status=RunStatus.RUNNING)
    agent = _build(tmp_path, sleep_seconds=120)
    wait = next(
        tool for tool in agent.default_tools if tool.name == "sleep_between_runs"
    )
    monkeypatch.setattr(sleep_module, "sleep", lambda seconds: agent.pipe.cancel())

    with pytest.raises(ExternalCallCancelledError):
        wait(Empty(), [])


def test_wait_observes_agent_pipe_cancellation(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    _write_source_run(sandbox, status=RunStatus.RUNNING)
    agent = _build(tmp_path, sleep_seconds=120.0)
    wait = next(
        tool for tool in agent.default_tools if tool.name == "sleep_between_runs"
    )
    agent.pipe.cancel()

    with pytest.raises(ExternalCallCancelledError):
        wait(input=Empty(), messages=[])


def test_librarian_invoke_persists_no_message_cycle_outputs(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    agent = _build(tmp_path)

    agent.invoke()

    run_files = list((sandbox.project_logs_dir() / "librarian").rglob("*.json"))
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
    sandbox = _sandbox(tmp_path)
    _write_source_run(sandbox)
    secret = "secret-value-123456789"
    endpoint = MockLLMEndpoint(
        [MockProviderError(f"Invalid api_key={secret}", status_code=401)]
    )
    agent = _build(
        tmp_path,
        endpoint=endpoint,
        token_growth_threshold=1,
        sleep_seconds=0.01,
    )

    with pytest.raises(LibrarianProviderRequestFailure) as raised:
        agent.invoke()

    assert isinstance(raised.value.__cause__, LLMAuthError)
    run_files = list((sandbox.project_logs_dir() / "librarian").rglob("*.json"))
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

    from roboz.models import All, Str

    result = sleep_between_runs(SleepBetweenRunsContext(seconds=0))(All(), [])
    assert isinstance(result, Str)
    assert result.value == "sleep_between_runs: slept=0.0s"


@pytest.mark.parametrize("override_snapshot", [False, True])
def test_librarian_overrides_each_tool_endpoint_independently(
    tmp_path, override_snapshot
):
    from roboz.llm import LLMEndpoint

    def forbidden(*args, **kwargs):
        pytest.fail("Building and inspecting must not initialize or call the client")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)),
        models=SimpleNamespace(list=forbidden),
        close=forbidden,
        materialize=forbidden,
    )
    default = LLMEndpoint(client=client, api_name="test", model_name="default")
    override = LLMEndpoint(client=client, api_name="test", model_name="override")
    sandbox = _sandbox(tmp_path)
    definition = _librarian(
        sandbox,
        {_WATCHED_AGENT},
        endpoint=default,
        capabilities=(
            ConversationSnapshots(
                endpoint=override if override_snapshot else None,
            ),
            MemoryConsolidation(
                endpoint=None if override_snapshot else override,
            ),
        ),
    )
    agent = definition.build()[0]

    snapshot, consolidation = agent.default_tools[:2]
    expected = (override, default) if override_snapshot else (default, override)
    assert snapshot.external_dependencies() == (expected[0],)
    assert snapshot.external_dependencies()[0] is expected[0]
    assert consolidation.external_dependencies() == (expected[1],)
    assert consolidation.external_dependencies()[0] is expected[1]
    assert agent.external_dependencies() == expected
    assert definition.external_dependencies() == expected
    assert not sandbox.project_dir().exists()
