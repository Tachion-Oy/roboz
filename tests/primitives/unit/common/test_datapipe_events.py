import json
from unittest.mock import Mock

import pytest

from roboz.agent.core import Agent
from roboz.tools import stop
from roboz.agent.subagent import SubagentCtx, run_subagent
from roboz.models import Empty, Message, Str
from roboz.runtime.observability import (
    FailureKind,
    LifecycleKind,
    RuntimeEventCategory,
    RuntimeEventLevel,
)
from roboz.runtime import sinks
from roboz.runtime.events import (
    MessageDeltaEvent,
    MessageEvent,
    RunLifecycleEvent,
    RuntimeEvent,
    ScriptOutputEvent,
)
from roboz.runtime.pipe import EventPipe
from roboz.runtime.persistence import RunStatus
from roboz.runtime.sinks import CliSink, PersistenceSink, default_event_sinks
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime import Output
from roboz.models import Role
from roboz.tooling.decorators import tool
from roboz.llm.endpoints import MockLLMEndpoint


def test_datapipe_emits_lifecycle_and_message_events_in_order() -> None:
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)

    pipe.initialize(dry_run=False, agent_name="event_agent")
    pipe(Message(role=Role.USER, content="hello"))
    pipe.emit_script_output("script line")
    pipe.finalize_run(status=RunStatus.COMPLETED)

    assert len(events) == 4
    assert isinstance(events[0], RunLifecycleEvent)
    assert events[0].kind == "started"
    assert events[0].agent_name == "event_agent"
    assert events[0].parent_agent_name is None
    assert events[0].api_name is None
    assert events[0].model_name is None
    assert events[0].max_context_tokens is None
    assert events[0].temperature is None
    assert events[0].output_format is None

    assert isinstance(events[1], MessageEvent)
    assert events[1].sequence == 2
    assert events[1].message.role == Role.USER
    assert events[1].message.content == "hello"

    assert isinstance(events[2], ScriptOutputEvent)
    assert events[2].sequence == 3
    assert events[2].content == "script line"

    assert isinstance(events[3], RunLifecycleEvent)
    assert events[3].sequence == 4
    assert events[3].kind == "stopped"
    assert events[3].parent_agent_name is None
    assert events[3].status == "completed"


def test_datapipe_constructor_event_sinks_receive_events() -> None:
    events: list[object] = []
    pipe = EventPipe(event_sinks=[events.append])

    pipe.initialize(dry_run=False, agent_name="event_agent")
    pipe.emit_message(Message(role=Role.USER, content="hello"))
    pipe.finalize_run(status=RunStatus.COMPLETED)

    assert len(events) == 3
    assert isinstance(events[0], RunLifecycleEvent)
    assert events[0].kind == "started"
    assert isinstance(events[1], MessageEvent)
    assert isinstance(events[2], RunLifecycleEvent)
    assert events[2].kind == "stopped"


def test_datapipe_emits_runtime_events_with_agent_context() -> None:
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)

    pipe.initialize(dry_run=False, agent_name="event_agent")
    pipe.emit_runtime_event(
        category=RuntimeEventCategory.LLM,
        kind=FailureKind.FAILED,
        level=RuntimeEventLevel.ERROR,
        message="provider failed",
        data={"endpoint": "fake"},
    )

    runtime_events = [event for event in events if isinstance(event, RuntimeEvent)]
    assert len(runtime_events) == 1
    assert runtime_events[0].category == "llm"
    assert runtime_events[0].kind == "failed"
    assert runtime_events[0].level == "error"
    assert runtime_events[0].agent_name == "event_agent"
    assert runtime_events[0].data == {"endpoint": "fake"}


def test_datapipe_message_delta_and_final_message_share_id_with_distinct_sequences() -> (
    None
):
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)

    pipe.initialize(dry_run=False, agent_name="event_agent")
    message_id = pipe.start_message()
    pipe.emit_message_delta("hel")
    pipe.emit_message_delta("lo")
    pipe.emit_message(Message(role=Role.ASSISTANT, content="hello"))

    deltas = [event for event in events if isinstance(event, MessageDeltaEvent)]
    messages = [event for event in events if isinstance(event, MessageEvent)]

    assert [delta.delta for delta in deltas] == ["hel", "lo"]
    assert [delta.chunk_index for delta in deltas] == [1, 2]
    assert all(delta.message_id == message_id for delta in deltas)
    assert all(delta.role == Role.ASSISTANT for delta in deltas)
    assert all(delta.agent_name == "event_agent" for delta in deltas)
    assert [delta.sequence for delta in deltas] == [2, 3]

    assert len(messages) == 1
    assert messages[0].message_id == message_id
    assert messages[0].sequence == 4


def test_roboz_constructor_event_sinks_receive_agent_events() -> None:
    collected: list[object] = []
    agent = Agent(
        interaction_mode=Output.API,
        name="explicit_sink_agent",
        tools=[stop],
        system_prompt="Stop immediately.",
        event_sinks=(collected.append,),
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
    )

    agent.invoke()

    lifecycle = [event for event in collected if isinstance(event, RunLifecycleEvent)]
    assert [event.kind for event in lifecycle] == ["started", "stopped"]
    assert all(event.agent_name == "explicit_sink_agent" for event in lifecycle)


def test_roboz_constructor_uses_supplied_event_pipe() -> None:
    collected: list[object] = []
    pipe = EventPipe(event_sinks=(collected.append,))
    agent = Agent(
        interaction_mode=Output.API,
        name="supplied_pipe_agent",
        tools=[stop],
        system_prompt="Stop immediately.",
        event_pipe=pipe,
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
    )

    assert agent.pipe is pipe
    agent.invoke()

    lifecycle = [event for event in collected if isinstance(event, RunLifecycleEvent)]
    assert [event.kind for event in lifecycle] == ["started", "stopped"]

    agent.pipe.cancel()
    assert pipe.cancelled
    pipe.interrupt()
    assert agent.pipe.interrupted


def test_roboz_constructor_rejects_event_pipe_and_event_sinks() -> None:
    with pytest.raises(ValueError, match="either event_pipe or event_sinks"):
        Agent(
            interaction_mode=Output.API,
            name="ambiguous_pipe_agent",
            tools=[stop],
            system_prompt="Stop immediately.",
            event_pipe=EventPipe(),
            event_sinks=(),
            agent_endpoint=MockLLMEndpoint([]),
        )


def test_cli_sink_default_creates_explicit_terminal_sink(monkeypatch) -> None:
    sink = CliSink.default()
    render = Mock()
    console_print = Mock()
    monkeypatch.setattr(sinks, "rich_print_message_to_terminal", render)
    monkeypatch.setattr(sinks.Console, "print", console_print)

    sink(MessageEvent(Message(role=Role.USER, content="hello"), sequence=1))
    sink(RunLifecycleEvent(kind="started", agent_name="cli_agent", sequence=2))

    render.assert_called_once()
    console_print.assert_called_once()


def test_cli_sink_prints_script_output(monkeypatch) -> None:
    sink = CliSink.default()
    console_print = Mock()
    monkeypatch.setattr(sinks.Console, "print", console_print)

    sink(ScriptOutputEvent(content="script line", sequence=1))

    console_print.assert_called_once_with("script line")


def test_persistence_sink_for_path_creates_conversation_file(tmp_path) -> None:
    sink = PersistenceSink.for_path(tmp_path / "runs")

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    sink(
        MessageEvent(
            message=Message(role=Role.USER, content="hello"),
            sequence=2,
        )
    )

    assert sink.conversations_location is not None
    assert sink.conversations_location.is_file()


def test_persistence_sink_ignores_script_output_without_message_schema(
    tmp_path,
) -> None:
    sink = PersistenceSink.for_path(tmp_path / "runs")

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    path = sink.conversations_location
    assert path is not None
    before = path.read_text(encoding="utf-8")

    sink(ScriptOutputEvent(content="script line", sequence=2))

    assert path.read_text(encoding="utf-8") == before


def test_persistence_sink_records_runtime_events_separately(tmp_path) -> None:
    sink = PersistenceSink.for_path(tmp_path / "runs")

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    sink(
        RuntimeEvent(
            category=RuntimeEventCategory.LLM,
            kind=FailureKind.TIMED_OUT,
            level=RuntimeEventLevel.ERROR,
            message="LLM call timed out",
            sequence=2,
            agent_name="agent",
            data={"endpoint": "fake"},
        )
    )

    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text())
    assert data["messages"] == []
    assert len(data["runtime_events"]) == 1
    assert data["runtime_events"][0]["category"] == "llm"
    assert data["runtime_events"][0]["kind"] == "timed_out"
    assert data["runtime_events"][0]["data"] == {"endpoint": "fake"}


def test_persistence_sink_filters_details_without_rewriting_run(
    tmp_path, monkeypatch
) -> None:
    sink = PersistenceSink.for_path(
        tmp_path / "runs", detail_predicate=lambda event: False
    )
    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    assert sink.conversations_location is not None
    write_run = Mock(wraps=sink._write_run)
    monkeypatch.setattr(sink, "_write_run", write_run)

    sink(
        MessageEvent(
            message=Message(role=Role.USER, content="routine"),
            sequence=2,
        )
    )
    sink(
        RuntimeEvent(
            category=RuntimeEventCategory.TOOL,
            kind=LifecycleKind.SUCCEEDED,
            level=RuntimeEventLevel.INFO,
            message="routine",
            sequence=3,
            agent_name="agent",
        )
    )

    write_run.assert_not_called()
    data = json.loads(sink.conversations_location.read_text())
    assert data["messages"] == []
    assert data["runtime_events"] == []


def test_persistence_sink_always_persists_lifecycle(tmp_path) -> None:
    sink = PersistenceSink.for_path(
        tmp_path / "runs", detail_predicate=lambda event: False
    )

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    sink(
        RunLifecycleEvent(
            kind="stopped", agent_name="agent", sequence=2, status="completed"
        )
    )

    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text())
    assert data["status"] == "completed"
    assert data["ended_at"] is not None


def test_default_event_sinks_passes_persistence_detail_predicate(tmp_path) -> None:
    event_sinks = default_event_sinks(
        data_path=tmp_path,
        include_cli=False,
        persistence_detail_predicate=lambda event: False,
    )
    (sink,) = event_sinks

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))
    sink(MessageEvent(Message(role=Role.USER, content="routine"), sequence=2))

    assert isinstance(sink, PersistenceSink)
    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text())
    assert data["messages"] == []


def test_default_event_sinks_can_be_passed_to_agent(tmp_path) -> None:
    sinks = default_event_sinks(data_path=tmp_path, include_cli=False)
    persistence_sink = next(sink for sink in sinks if isinstance(sink, PersistenceSink))
    agent = Agent(
        interaction_mode=Output.API,
        name="default_sinks_agent",
        tools=[stop],
        system_prompt="Stop immediately.",
        event_sinks=sinks,
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
    )

    agent.invoke()

    assert persistence_sink.conversations_location is not None
    assert persistence_sink.conversations_location.is_file()


def test_datapipe_unsubscribe_closure_stops_delivery() -> None:
    pipe = EventPipe()
    events: list[object] = []
    unsubscribe = pipe.add_sink(events.append)

    pipe.initialize(dry_run=False, agent_name="event_agent")
    unsubscribe()
    pipe.emit_message(Message(role=Role.USER, content="hello"))
    pipe.finalize_run(status=RunStatus.COMPLETED)

    assert len(events) == 1
    assert isinstance(events[0], RunLifecycleEvent)
    assert events[0].kind == "started"


def test_datapipe_exposes_and_resets_cancellation_state_per_initialize() -> None:
    pipe = EventPipe()
    assert pipe.cancelled is False

    pipe.cancel()
    assert pipe.cancelled is True

    pipe.initialize(dry_run=True, agent_name="event_agent")
    assert pipe.cancelled is False


def test_datapipe_exposes_and_resets_interrupt_state_per_initialize() -> None:
    pipe = EventPipe()
    assert pipe.interrupted is False

    pipe.interrupt()
    assert pipe.interrupted is True

    pipe.initialize(dry_run=True, agent_name="event_agent")
    assert pipe.interrupted is False


def test_datapipe_started_event_includes_endpoint_metadata() -> None:
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)

    pipe.initialize(
        dry_run=False,
        agent_name="event_agent",
        api_name="openrouter",
        model_name="gpt-4.1-mini",
        max_context_tokens=200_000,
        temperature=0.1,
        output_format="json",
    )

    assert len(events) == 1
    assert isinstance(events[0], RunLifecycleEvent)
    assert events[0].kind == "started"
    assert events[0].agent_name == "event_agent"
    assert events[0].parent_agent_name is None
    assert events[0].api_name == "openrouter"
    assert events[0].model_name == "gpt-4.1-mini"
    assert events[0].max_context_tokens == 200_000
    assert events[0].temperature == 0.1
    assert events[0].output_format == "json"
    pipe.finalize_run(status=RunStatus.COMPLETED)


def test_agent_output_modes_do_not_wire_terminal_subscribers(monkeypatch) -> None:
    render = Mock()
    console_print = Mock()
    monkeypatch.setattr(sinks, "rich_print_message_to_terminal", render)
    monkeypatch.setattr(sinks.Console, "print", console_print)

    for output in (Output.CLI, Output.API):
        agent = Agent(
            interaction_mode=output,
            name=f"{output.name.lower()}_agent",
            tools=[stop],
            system_prompt="Stop immediately.",
            agent_endpoint=MockLLMEndpoint(
                [{"action": "stop", "rationale": "done", "value": "ok"}]
            ),
        )
        agent.invoke()

    render.assert_not_called()
    console_print.assert_not_called()


def test_nested_subagent_lifecycle_events_reach_explicit_event_sinks() -> None:
    collected: list[object] = []

    def sink(event: object) -> None:
        collected.append(event)

    event_sinks = (sink,)
    child = Agent(
        interaction_mode=Output.API,
        name="child_agent",
        tools=[stop],
        system_prompt="Child.",
        event_sinks=event_sinks,
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "stop",
                    "rationale": "done child",
                    "value": "child done",
                }
            ]
        ),
    )
    delegate = run_subagent(SubagentCtx(child)).copy(name="delegate")
    parent = Agent(
        interaction_mode=Output.API,
        name="parent_agent",
        tools=[delegate, stop],
        system_prompt="Parent.",
        event_sinks=event_sinks,
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "delegate", "rationale": "run child"},
                {"action": "stop", "rationale": "done parent", "value": "ok"},
            ]
        ),
    )
    parent.invoke()

    lifecycle = [event for event in collected if isinstance(event, RunLifecycleEvent)]
    assert len(lifecycle) == 4

    parent_started = lifecycle[0]
    child_started = lifecycle[1]
    child_stopped = lifecycle[2]
    parent_stopped = lifecycle[3]

    assert (parent_started.kind, parent_started.agent_name) == (
        "started",
        "parent_agent",
    )
    assert parent_started.parent_agent_name is None

    assert (child_started.kind, child_started.agent_name) == ("started", "child_agent")
    assert child_started.parent_agent_name == "parent_agent"

    assert (child_stopped.kind, child_stopped.agent_name) == ("stopped", "child_agent")
    assert child_stopped.parent_agent_name == "parent_agent"

    assert (parent_stopped.kind, parent_stopped.agent_name) == (
        "stopped",
        "parent_agent",
    )
    assert parent_stopped.parent_agent_name is None


def test_persistence_sink_writes_file_on_started_event_alone(tmp_path) -> None:
    """A run must be on disk from the 'started' lifecycle event, before any
    message and without a clean stop (regression: librarian wrote zero files)."""
    sink = PersistenceSink.for_path(tmp_path / "runs")

    sink(RunLifecycleEvent(kind="started", agent_name="agent", sequence=1))

    assert sink.conversations_location is not None
    assert sink.conversations_location.is_file()
    run = json.loads(sink.conversations_location.read_text())
    assert run["agent_name"] == "agent"
    assert run["status"] == "running"


def test_datapipe_delivers_no_message_truncation_to_sinks() -> None:
    """NO_MESSAGE governs the agent's LLM context, not the record. The pipe must
    still deliver such messages to sinks (regression: they were dropped)."""
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)

    pipe.initialize(dry_run=False, agent_name="event_agent")
    pipe(Message(role=Role.USER, content="kept-in-log", truncation=NO_MESSAGE))

    messages = [e for e in events if isinstance(e, MessageEvent)]
    assert len(messages) == 1
    assert messages[0].message.content == "kept-in-log"
    assert messages[0].message.truncation == NO_MESSAGE


def test_persistence_records_no_message_tool_output_during_invoke(tmp_path) -> None:
    """An agent whose tool returns NO_MESSAGE must still have that output in its
    persisted log (this is the librarian's situation end to end)."""

    @tool
    def noisy(input: Empty, messages: list[Message]) -> Str:
        return Str(value="operational-noise", truncation=NO_MESSAGE)

    event_sinks = default_event_sinks(data_path=tmp_path, include_cli=False)
    persistence_sink = next(s for s in event_sinks if isinstance(s, PersistenceSink))
    agent = Agent(
        interaction_mode=Output.API,
        name="noisy_agent",
        tools=[noisy, stop],
        system_prompt="Make noise, then stop.",
        event_sinks=event_sinks,
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "noisy", "rationale": "make noise"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
    )

    agent.invoke()

    assert persistence_sink.conversations_location is not None
    content = persistence_sink.conversations_location.read_text()
    assert "operational-noise" in content
