import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Thread
from typing import Any

import pytest

from roboz.agent._notifications import (
    INTERRUPT_PROMPT_TO_USER,
    INTERRUPTED_GENERATION_CONTEXT,
    LLM_PROVIDER_REQUEST_RETRY_PROMPT,
)
from roboz.agent.core import Agent
from roboz.exceptions import (
    ExternalCallInterruptedError,
    LLMCallCancelledError,
    LLMProviderUnavailableError,
    StopAgent,
)
from roboz.llm.endpoints import MockLLMEndpoint, MockProviderError
from roboz.models import (
    BaseNames,
    Empty,
    Int,
    Invoke,
    Message,
    MessageKind,
    Role,
    Stop,
    Str,
)
from roboz.models.truncation import ERROR_RETRY, NO_MESSAGE, Severity
from roboz.runtime import EventPipe, Output, PersistenceSink, RuntimeEvent
from roboz.skill.core import Skill
from roboz.tooling.decorators import factory, tool
from roboz.tooling.dependencies import FactoryCtx
from roboz.tools import stop, stop_after

call_counts = defaultdict(list)


@tool
def default_tool(input: Empty, messages: list[Message]) -> Empty:
    call_counts["default_tool"].append(1)
    return Empty()


### Consolidation: two tools chained to one
@tool
def tool_a(input: Empty, messages: list[Message]) -> Int:
    call_counts["a"].append(1)
    return Int(value=1)


@tool
def tool_b(input: Int, messages: list[Message]) -> Int:
    call_counts["b"].append(1)
    return Int(value=2)


@tool(chained_to=[tool_a, tool_b])
def tool_c(input: Int, messages: list[Message]) -> Int:
    call_counts["c"].append(1)
    return Int(value=input.value)


### Fork: single tool splits
@tool
def tool_d(input: Int, messages: list[Message]) -> Int | Str:
    return Int(value=input.value)


@tool(chained_to=tool_d, chain_condition=lambda x: int(x.value) > 1)
def tool_e(input: Int, messages: list[Message]) -> Int:
    return Int(value=input.value)


@tool(chained_to=tool_d, chain_condition=lambda x: abs(int(x.value)) > 5)
def tool_f(input: Str, messages: list[Message]) -> Str:
    return Str(value=input.value)


@tool
def tool_g(input: Int, messages: list[Message]) -> Int:
    return Int(value=input.value)


@tool
def noop_entry(input: Empty, messages: list[Message]) -> Str:
    """Simple agent action for default-tool integration (no passive chains)."""
    return Str(value="ok")


@dataclass(frozen=True)
class _DefaultEmitCtx(FactoryCtx):
    counter: list[int]


@factory
def default_emit_integration(
    input: Empty, messages: list[Message], ctx: _DefaultEmitCtx
) -> Str:
    """Default-tool stub: alternates truncation severities so the loop can be asserted."""
    box = ctx.counter
    box[0] += 1
    if box[0] % 2 == 1:
        return Str(
            value="ping",
            truncation=NO_MESSAGE,
        )
    return Str(
        value="ping",
        truncation=ERROR_RETRY,
    )


### skill and tool ###


@tool
def skill_tool(input: Str, messages: list[Message]) -> Str:
    return Str(value="default")


skill = Skill(
    name="test_skill",
    description="A test skill",
    instructions="Do something!",
    tools=[tool_a, skill_tool],
)

auto_skill = Skill(
    name="test_auto_skill",
    description="A test skill to be called automatically",
    instructions="Do something automatically!",
    tools=[tool_a, skill_tool],
)


@pytest.fixture
def agent():
    tools = [tool_a, tool_b, tool_c, tool_d, tool_e, tool_f, stop]
    script = [
        dict(action="tool_a", rationale=""),
        dict(action="tool_b", rationale="", value=1),
        dict(action="test_skill", rationale="calling skill"),
        dict(
            action="test_skill",
            rationale="calling skill twice to see if something breaks",
        ),
        dict(action="stop", rationale="", value="Run finished!"),
    ]
    return Agent(
        interaction_mode=None,
        name="fixture_agent",
        description="Fixture agent description.",
        tools=tools,
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(script),
        custom_prompt_user_tool=tool_g,
        default_tools=[default_tool],
        skills=[skill],
        auto_loaded_skills=[auto_skill],
        initial_messages=None,
    )


def test_init(agent):
    assert isinstance(agent.pipe, EventPipe)
    assert len(agent.active_tools) + len(agent.passive_tools) == 10
    active_tool_names = [t.name for t in agent.active_tools.values()]
    passive_tool_names = [t.name for t in agent.passive_tools.values()]

    assert "tool_a" in active_tool_names
    assert "tool_b" in active_tool_names
    assert "tool_d" in active_tool_names
    assert "test_skill" in active_tool_names
    assert "skill_tool" not in active_tool_names
    assert "stop" in active_tool_names
    assert "tool_c" in passive_tool_names
    assert "tool_e" in passive_tool_names
    assert "tool_f" in passive_tool_names
    assert "prompt_agent" == agent.master_tool.name


def test_get_next_tool_invoke_valid(agent):
    output = Invoke(action="tool_a", rationale="test")
    next_tool = agent.get_next_tool(tool_a, output)
    assert next_tool.name == "tool_a"


def test_get_next_tool_invoke_invalid(agent):
    output = Invoke(action="non_existent", rationale="test")
    with pytest.raises(RuntimeError, match="does not exist"):
        agent.get_next_tool(tool_a, output)


def test_consolidation(agent):
    # tool_a -> tool_c
    output_a = tool_a(input=Empty(), messages=[])
    next_tool = agent.get_next_tool(tool_a, output_a)
    assert next_tool.name == "tool_c"
    result = next_tool(input=output_a, messages=[])
    assert result.value == 1

    # tool_b -> tool_c
    output_b = tool_b(input=Int(value=99), messages=[])
    next_tool_2 = agent.get_next_tool(tool_b, output_b)
    assert next_tool_2.name == "tool_c"
    result_2 = next_tool_2(input=output_b, messages=[])
    assert result_2.value == 2


def test_fork_no_match(agent):
    output, _ = agent.invoke()
    assert output.value == "Run finished!"


@tool
def raise_stop_agent(input: Empty, messages: list[Message]) -> Str:
    raise StopAgent


@tool
def raise_llm_cancelled(input: Empty, messages: list[Message]) -> Str:
    raise LLMCallCancelledError("cancelled")


@tool
def raise_interrupted_once(input: Empty, messages: list[Message]) -> Empty:
    if not call_counts["raise_interrupted_once"]:
        call_counts["raise_interrupted_once"].append(1)
        raise ExternalCallInterruptedError("interrupted")
    return Empty()


def test_invoke_stop_agent_finalizes_run_cancelled(
    tmp_path: Path,
) -> None:
    roboz_root = tmp_path / "roboz_data"
    sink = PersistenceSink.for_path(roboz_root)
    agent = Agent(
        interaction_mode=None,
        name="stop_agent_run",
        event_sinks=(sink,),
        tools=[raise_stop_agent],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="raise_stop_agent", rationale="stop")]
        ),
        initial_messages=None,
    )
    agent.invoke()
    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "cancelled"
    assert data["ended_at"] is not None


def test_invoke_llm_cancelled_error_finalizes_run_cancelled(
    tmp_path: Path,
) -> None:
    roboz_root = tmp_path / "roboz_data"
    sink = PersistenceSink.for_path(roboz_root)
    agent = Agent(
        interaction_mode=None,
        name="llm_cancelled_run",
        event_sinks=(sink,),
        tools=[raise_llm_cancelled],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="raise_llm_cancelled", rationale="stop")]
        ),
        initial_messages=None,
    )
    agent.invoke()
    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "cancelled"
    assert data["ended_at"] is not None


def test_invoke_pipe_cancelled_between_tools_stops_before_next_tool(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    agent: Agent

    @tool
    def cancel_after_first(input: Empty, messages: list[Message]) -> Empty:
        calls.append("cancel_after_first")
        agent.pipe.cancel()
        return Empty()

    @tool
    def must_not_run(input: Empty, messages: list[Message]) -> Stop:
        calls.append("must_not_run")
        return Stop()

    sink = PersistenceSink.for_path(tmp_path / "roboz_data")
    agent = Agent(
        interaction_mode=None,
        name="cancel_between_tools",
        is_agentic=False,
        default_tools=[cancel_after_first, must_not_run],
        tools=[],
        system_prompt="",
        agent_endpoint=None,
        initial_messages=None,
        event_sinks=(sink,),
    )

    agent.invoke()

    assert calls == ["cancel_after_first"]
    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "cancelled"


def test_invoke_interrupt_prompts_user_and_resumes(bind_user_io) -> None:
    call_counts["raise_interrupted_once"].clear()
    io = bind_user_io(["detour reply"])
    agent = Agent(
        interaction_mode=Output.API,
        name="interrupt_resume_run",
        tools=[raise_interrupted_once, stop],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "raise_interrupted_once", "rationale": "interrupt now"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )

    output, messages = agent.invoke()

    assert output.value == "ok"
    assert call_counts["raise_interrupted_once"] == [1]
    assert io.prompts[-1] == INTERRUPT_PROMPT_TO_USER

    structured_user_messages: list[tuple[int, Message, dict[str, object]]] = []
    for index, message in enumerate(messages):
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            structured_user_messages.append((index, message, payload))

    interrupt_markers = [
        (index, payload)
        for index, message, payload in structured_user_messages
        if message.message_kind == MessageKind.INTERRUPTED_GENERATION
    ]
    assert len(interrupt_markers) == 1
    marker_index, marker_payload = interrupt_markers[0]
    assert marker_payload.get(BaseNames.VALUE_FIELD) == INTERRUPTED_GENERATION_CONTEXT

    follow_up_replies = [
        (index, payload)
        for index, _, payload in structured_user_messages
        if payload.get(BaseNames.CALLER_FIELD) == "prompt_user"
        and payload.get(BaseNames.VALUE_FIELD) == "detour reply"
    ]
    assert len(follow_up_replies) == 1
    assert marker_index < follow_up_replies[0][0]


def test_invoke_provider_request_error_prompts_user_and_resumes(
    bind_user_io, tmp_path: Path
) -> None:
    io = bind_user_io(["retry"])
    sink = PersistenceSink.for_path(tmp_path)
    agent = Agent(
        interaction_mode=Output.API,
        name="provider_request_retry_run",
        tools=[stop],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [
                MockProviderError("Invalid API key", status_code=401),
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
        initial_messages=None,
        event_sinks=(sink,),
    )

    output, messages = agent.invoke()

    assert output.value == "ok"
    assert io.prompts == [LLM_PROVIDER_REQUEST_RETRY_PROMPT]
    assert any("retry" in message.content for message in messages)
    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    failure = next(
        event
        for event in data["runtime_events"]
        if event["category"] == "llm" and event["kind"] == "failed"
    )
    assert failure["level"] == "warning"
    assert failure["data"]["error_kind"] == "auth"


def test_invoke_provider_5xx_remains_terminal_failure(tmp_path: Path) -> None:
    sink = PersistenceSink.for_path(tmp_path)
    agent = Agent(
        interaction_mode=None,
        name="provider_5xx_failure_run",
        tools=[stop],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [MockProviderError("Provider unavailable", status_code=500)]
        ),
        initial_messages=None,
        event_sinks=(sink,),
    )

    with pytest.raises(LLMProviderUnavailableError):
        agent.invoke()

    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    failure = next(
        event
        for event in data["runtime_events"]
        if event["category"] == "llm" and event["kind"] == "failed"
    )
    assert failure["level"] == "error"


def test_agent_without_persistence_sink_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    script = [dict(action="stop", rationale="", value="done")]

    agent_1 = Agent(
        interaction_mode=None,
        name="warn_agent_1",
        tools=[stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(script),
        initial_messages=None,
    )
    agent_2 = Agent(
        interaction_mode=None,
        name="warn_agent_2",
        tools=[stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(script),
        initial_messages=None,
    )
    agent_1.invoke()
    agent_2.invoke()

    assert not any(tmp_path.rglob("*.json"))


def test_agent_emits_tool_events_to_configured_sinks() -> None:
    events: list[object] = []
    agent = Agent(
        interaction_mode=None,
        name="injected_pipe_agent",
        tools=[stop],
        system_prompt="system",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
        initial_messages=None,
        event_sinks=(events.append,),
    )

    output, _ = agent.invoke()

    assert output.value == "ok"
    tool_events = [
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.category == "tool"
    ]
    assert [event.kind for event in tool_events] == [
        "started",
        "succeeded",
        "started",
        "succeeded",
    ]
    assert all(event.agent_name == "injected_pipe_agent" for event in tool_events)
    assert all(
        event.data is None
        or not {"output_preview", "error_preview"}.intersection(event.data)
        for event in tool_events
    )
    sequences = [event.sequence for event in events if hasattr(event, "sequence")]
    assert sequences == sorted(sequences)


def test_stop_after_single_cleanup_runs_before_stop():
    calls: list[str] = []

    @tool
    def cleanup(input: Str, messages: list[Message]) -> Str:
        calls.append("cleanup")
        return Str(value="cleanup-overrode-value")

    agent = Agent(
        interaction_mode=None,
        name="stop_after_single_cleanup",
        tools=[stop_after(cleanup)],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="done")]
        ),
        initial_messages=None,
    )

    out, messages = agent.invoke()
    assert out.value == "done"
    assert calls == ["cleanup"]

    cleanup_events = []
    for m in messages:
        if m.role != Role.USER:
            continue
        try:
            payload = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("caller") == "cleanup":
            cleanup_events.append(payload)
    assert len(cleanup_events) == 1


def test_stop_after_cleanup_list_runs_in_order():
    calls: list[str] = []

    @tool
    def cleanup_one(input: Str, messages: list[Message]) -> Str:
        calls.append("cleanup_one")
        return Str(value=input.value)

    @tool
    def cleanup_two(input: Str, messages: list[Message]) -> Str:
        calls.append("cleanup_two")
        return Str(value=input.value)

    agent = Agent(
        interaction_mode=None,
        name="stop_after_cleanup_list",
        tools=[stop_after([cleanup_one, cleanup_two])],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="done")]
        ),
        initial_messages=None,
    )

    out, _ = agent.invoke()
    assert out.value == "done"
    assert calls == ["cleanup_one", "cleanup_two"]


def test_stop_after_custom_final_stop_tool_handles_empty_cleanup_output():
    @tool
    def cleanup_empty(input: Str, messages: list[Message]) -> Empty:
        return Empty()

    @tool
    def final_stop_from_empty(input: Empty, messages: list[Message]) -> Stop:
        return Stop(value="custom-final-stop")

    stop_tools = stop_after(cleanup_empty, final_stop_tool=final_stop_from_empty)
    assert stop_tools[0].name == "final_stop_from_empty"
    assert stop_tools[0].description == final_stop_from_empty.description

    agent = Agent(
        interaction_mode=None,
        name="stop_after_empty_cleanup",
        tools=[stop_tools],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="final_stop_from_empty", rationale="", value="preserved")]
        ),
        initial_messages=None,
    )

    out, _ = agent.invoke()
    assert out.value == "custom-final-stop"


def test_stop_after_clears_preserved_value_after_finalization() -> None:
    @tool
    def cleanup(input: Str, messages: list[Message]) -> Str:
        return input

    entry, _, finalize = stop_after(cleanup)
    def run(value: str) -> Stop:
        entry(input=Str(value=value), messages=[])
        result = finalize(input=Empty(), messages=[])
        assert isinstance(result, Stop)
        return result

    assert run("first").value == "first"
    assert run("second").value == "second"

    with pytest.raises(RuntimeError, match="without an active stop value"):
        finalize(input=Empty(), messages=[])


def test_stop_after_isolates_preserved_value_for_shared_concurrent_tools() -> None:
    @tool
    def cleanup(input: Str, messages: list[Message]) -> Str:
        return input

    entry, _, finalize = stop_after(cleanup)
    rendezvous = Barrier(2)
    results: dict[str, str | None] = {}

    def run(value: str) -> None:
        entry(input=Str(value=value), messages=[])
        rendezvous.wait(timeout=1)
        result = finalize(input=Empty(), messages=[])
        assert isinstance(result, Stop)
        results[value] = result.value

    threads = [Thread(target=run, args=(value,)) for value in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert results == {"first": "first", "second": "second"}


def test_stop_after_cleanup_not_run_on_abrupt_cancellation():
    calls: list[str] = []

    @tool
    def abort(input: Empty, messages: list[Message]) -> Str:
        raise StopAgent

    @tool
    def cleanup(input: Str, messages: list[Message]) -> Str:
        calls.append("cleanup")
        return Str(value=input.value)

    agent = Agent(
        interaction_mode=None,
        name="stop_after_cancelled",
        tools=[abort, stop_after(cleanup)],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint([dict(action="abort", rationale="cancel")]),
        initial_messages=None,
    )

    out, _ = agent.invoke()
    assert out.value is None
    assert calls == []


def test_fork_match_single(agent):
    output = Int(value=2)
    next_tool = agent.get_next_tool(tool_d, output)

    assert next_tool.name == "tool_e"
    output = Int(value=-10)
    next_tool = agent.get_next_tool(tool_d, output)
    assert next_tool.name == "tool_f"


def test_fork_match_multiple_error(agent):
    output = Int(value=15)
    with pytest.raises(RuntimeError):
        agent.get_next_tool(tool_d, output)


def test_calling(agent):
    [count.clear() for count in call_counts.values()]
    output, messages = agent.invoke()
    assert sum(call_counts["a"]) == 1
    assert sum(call_counts["b"]) == 1
    assert sum(call_counts["c"]) == 2
    assert sum(call_counts["default_tool"]) == 5

    assert "skill_tool" in [t.name for t in agent.active_tools.values()]
    assert messages[-2].role == Role.ASSISTANT
    assert messages[-1].role == Role.USER
    assert json.loads(messages[-1].content)["value"] == "Run finished!"


@pytest.mark.parametrize("empty_prompt", ["", "   ", "\n\t"])
def test_agentic_agent_requires_non_empty_system_prompt(empty_prompt):
    with pytest.raises(ValueError, match="empty system prompt"):
        Agent(
            interaction_mode=None,
            name="empty_prompt_agent",
            tools=[stop],
            system_prompt=empty_prompt,
            agent_endpoint=MockLLMEndpoint(
                [dict(action="stop", rationale="", value="unused")]
            ),
            initial_messages=None,
        )


def test_disabled_automatic_tool_prompt_uses_only_configured_system_prompt():
    system_prompt = "Use only these configured instructions."
    agent = Agent(
        interaction_mode=None,
        name="manual_tool_prompt_agent",
        tools=[stop],
        system_prompt=system_prompt,
        automatic_tool_prompt=False,
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
        initial_messages=None,
    )

    _, messages = agent.invoke()

    system_messages = [message for message in messages if message.role == Role.SYSTEM]
    assert [message.content for message in system_messages] == [system_prompt]
    assert agent.full_system_prompt == system_prompt


def test_non_agentic_agent_allows_empty_system_prompt():
    @tool
    def default_stop(input: Empty, messages: list[Message]) -> Stop:
        return Stop(value="done")

    agent = Agent(
        interaction_mode=None,
        name="non_agentic_empty_prompt",
        is_agentic=False,
        tools=[stop],
        system_prompt="",
        default_tools=[default_stop],
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="unused")]
        ),
        initial_messages=None,
    )
    out, _ = agent.invoke()
    assert out.value == "done"


def test_duplicate_tool_name_validation():
    @tool
    def duplicate_name_tool(input: Empty, messages: list[Message]) -> Str:
        return Str(value="tool")

    @tool
    def skill_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value="skill")

    skill = Skill(
        name="duplicate_name_tool",
        description="A skill with duplicate name",
        instructions="Do something!",
        tools=[skill_tool],
    )

    with pytest.raises(ValueError, match="Duplicate tool name"):
        Agent(
            interaction_mode=None,
            name="dup_test",
            tools=[duplicate_name_tool],
            skills=[skill],
            system_prompt="prompt",
            agent_endpoint=MockLLMEndpoint(
                [dict(action="stop", rationale="", value="unused")]
            ),
            initial_messages=None,
        )


def test_duplicate_tool_name_validation_two_tools():
    @tool
    def duplicate_name_tool(input: Empty, messages: list[Message]) -> Str:
        return Str(value="tool")

    @tool
    def duplicate_name_tool_2(input: Empty, messages: list[Message]) -> Str:
        return Str(value="tool2")

    duplicate_name_tool_2.name = "duplicate_name_tool"

    with pytest.raises(ValueError, match="Duplicate tool name"):
        Agent(
            interaction_mode=None,
            name="dup_test_two",
            tools=[duplicate_name_tool, duplicate_name_tool_2],
            system_prompt="prompt",
            agent_endpoint=MockLLMEndpoint(
                [dict(action="stop", rationale="", value="unused")]
            ),
            initial_messages=None,
        )


def test_skill_rejects_activating_tool_with_same_name():
    @tool
    def duplicate_skill_name(input: Empty, messages: list[Message]) -> Str:
        return Str(value="tool")

    skill = Skill(
        name="duplicate_skill_name",
        description="A skill with a same-named tool",
        instructions="Do something!",
        tools=[duplicate_skill_name],
    )
    responses: list[dict[str, Any] | Exception] = [
        dict(action="duplicate_skill_name", rationale="load skill")
    ]
    agent = Agent(
        interaction_mode=None,
        name="duplicate_skill_tool_test",
        tools=[stop],
        skills=[skill],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(responses),
        initial_messages=None,
    )

    with pytest.raises(ValueError, match="Duplicate tool name"):
        agent.invoke()


def test_duplicate_name_ok_if_passive():
    @tool
    def active_tool(input: Empty, messages: list[Message]) -> Str:
        return Str(value="active")

    @tool(chained_to=active_tool, chain_condition=lambda x: True)
    def passive_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value="passive")

    passive_tool.name = "active_tool"

    Agent(
        interaction_mode=None,
        name="passive_dup_ok",
        tools=[active_tool, passive_tool],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="unused")]
        ),
        initial_messages=None,
    )


def test_copy_agent(agent):
    agent_copy = agent.copy()

    assert isinstance(agent_copy, Agent)
    assert agent_copy is not agent
    assert len(agent_copy.active_tools) == len(agent.active_tools)
    assert len(agent_copy.passive_tools) == len(agent.passive_tools)
    assert len(agent_copy._skills) == len(agent.skills)
    assert "test_skill" in agent_copy._skills
    assert agent_copy.system_prompt == agent.system_prompt
    assert agent_copy.description == agent.description
    # full_system_prompt is derived from active_tools; tool order can vary, so check
    # that both contain the same tool names rather than exact string equality.
    # Auto-loaded skills are excluded from the prompt (redundant—they're loaded at start).
    orig_tool_names = {t.name for t in agent.active_tools.values()}
    copy_tool_names = {t.name for t in agent_copy.active_tools.values()}
    assert orig_tool_names == copy_tool_names
    auto_loaded_names = set(agent._auto_loaded_skills)
    for name in orig_tool_names:
        if name not in auto_loaded_names:
            assert name in agent_copy.full_system_prompt
    assert agent_copy.master_tool.name == agent.master_tool.name
    assert agent_copy.pipe is agent.pipe


def test_copy_agent_uses_supplied_event_pipe(agent):
    pipe = EventPipe()

    agent_copy = agent.copy(event_pipe=pipe)

    assert agent_copy.pipe is pipe


# --- Agent.copy(**overrides): optional kwargs replace copied fields -----------------


def test_copy_agent_overrides_system_prompt(agent):
    agent_copy = agent.copy(system_prompt="replaced")
    assert agent_copy.system_prompt == "replaced"
    assert agent.system_prompt != "replaced"
    assert "replaced" in agent_copy.full_system_prompt


def test_copy_agent_overrides_name(agent):
    agent_copy = agent.copy(name="renamed_agent")
    assert agent_copy.name == "renamed_agent"
    assert agent.name == "fixture_agent"


def test_copy_agent_overrides_description(agent):
    agent_copy = agent.copy(description="Updated copy description.")
    assert agent_copy.description == "Updated copy description."
    assert agent.description == "Fixture agent description."


def test_copy_agent_overrides_custom_prompt_user_tool_and_is_agentic(agent):
    copied_g = tool_g.copy(name="tool_g_copy")
    agent_copy = agent.copy(custom_prompt_user_tool=copied_g, is_agentic=False)
    assert agent_copy.prompt_user_tool.name == "tool_g_copy"
    assert agent_copy.is_agentic is False
    assert agent.is_agentic is True
    assert agent.prompt_user_tool.name == "tool_g"


def test_default_tools_run_in_order():
    calls: list[str] = []

    @tool
    def entry(input: Str, messages: list[Message]) -> Str:
        calls.append("entry")
        return Str(value="x")

    @tool
    def d1(input: Empty, messages: list[Message]) -> Str:
        calls.append("d1")
        return Str(value="1")

    @tool
    def d2(input: Str, messages: list[Message]) -> Str:
        calls.append("d2")
        return Str(value=input.value + "2")

    scripted = [
        dict(action="entry", rationale="", value="entry"),
        dict(action="stop", rationale="", value="ok"),
    ]
    a = Agent(
        interaction_mode=None,
        name="default_tools_order",
        tools=[entry, stop],
        system_prompt="prompt",
        default_tools=[d1, d2],
        agent_endpoint=MockLLMEndpoint(scripted),
        initial_messages=None,
    )

    out, _ = a.invoke()
    assert out.value == "ok"
    # Default tools are scheduled when there is no chained successor.
    # With startup driven through get_next_tool(None, Empty()), they run
    # before the first agent action and again after non-chained tool outputs.
    assert calls == ["d1", "d2", "entry", "d1", "d2"]


def test_default_tool_integration_mixed_truncation():
    """Default tool runs several times in the real Agent loop and may emit mixed severities."""
    scripted = [
        dict(action="noop_entry", rationale=""),
        dict(action="noop_entry", rationale=""),
        dict(action="stop", rationale="", value="done"),
    ]
    endpoint = MockLLMEndpoint(scripted)
    emitter = default_emit_integration(_DefaultEmitCtx(counter=[0]))
    agent = Agent(
        interaction_mode=None,
        name="mixed_truncation",
        tools=[noop_entry, stop],
        system_prompt="test",
        default_tools=[emitter],
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    _, messages = agent.invoke()

    def _severity(m: Message):
        if isinstance(m.truncation, list):
            return m.truncation[0].severity
        return m.truncation.severity

    emitter_msgs: list[Message] = []
    for m in messages:
        if m.role != Role.USER:
            continue
        try:
            data = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("caller") == "default_emit_integration":
            emitter_msgs.append(m)

    assert len(emitter_msgs) >= 2
    severities = [_severity(m) for m in emitter_msgs]
    assert Severity.REMOVE in severities
    assert any(s != Severity.REMOVE for s in severities)


def test_auto_loaded_skills_excluded_from_system_prompt(agent):
    """Auto-loaded skills must not appear as available tools in the system prompt,
    since they are loaded automatically—reduces redundant context."""
    assert "test_auto_skill" not in agent.full_system_prompt
    # Invocable skill should still appear
    assert "test_skill" in agent.full_system_prompt


def test_auto_load_skills_emit_structured_kinds(agent):
    _, messages = agent.invoke()
    user_payloads: list[tuple[Message, dict]] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            user_payloads.append((message, payload))

    banners = [
        payload
        for message, payload in user_payloads
        if message.message_kind == MessageKind.AUTO_LOAD_BANNER
    ]
    assert len(banners) == 1

    auto_skill_messages = [
        payload
        for message, payload in user_payloads
        if message.message_kind == MessageKind.AUTO_LOADED_SKILL
    ]
    assert len(auto_skill_messages) == 1
    assert BaseNames.KIND_FIELD not in banners[0]
    assert BaseNames.KIND_FIELD not in auto_skill_messages[0]
    auto_skill_value = auto_skill_messages[0][BaseNames.VALUE_FIELD]
    assert "## Available Tools" in auto_skill_value
    assert "skill_tool" in auto_skill_value
    assert "input Pydantic model" in auto_skill_value
    assert "action" in auto_skill_value
    assert "rationale" in auto_skill_value


def test_get_stored_message_wraps_markdown_as_startup_context(tmp_path: Path):
    markdown = "# Snapshot\n\nRemember this context."
    md_file = tmp_path / "snapshot.md"
    md_file.write_text(markdown)

    message = Agent._get_stored_message(md_file)
    assert message is not None
    assert message.role == Role.USER
    assert message.message_kind == MessageKind.STARTUP_CONTEXT
    payload = json.loads(message.content)
    assert payload[BaseNames.VALUE_FIELD] == markdown


def test_get_stored_message_uses_latest_markdown_in_folder(tmp_path: Path):
    (tmp_path / "20260101.md").write_text("# Old")
    latest_content = "# New\n\nNewest context."
    (tmp_path / "20260102.md").write_text(latest_content)

    message = Agent._get_stored_message(tmp_path)
    assert message is not None
    assert message.message_kind == MessageKind.STARTUP_CONTEXT
    payload = json.loads(message.content)
    assert payload[BaseNames.VALUE_FIELD] == latest_content


def test_skill_depends_on_allows_load_after_prerequisite():
    """Loading a skill with depends_on succeeds when the dependency was loaded first."""
    base = Skill(
        name="dep_base_skill",
        description="base",
        instructions="base instructions",
        tools=[tool_a],
    )
    child = Skill(
        name="dep_child_skill",
        description="child",
        instructions="child instructions",
        tools=[tool_b],
        depends_on=base,
    )
    script = [
        dict(action="dep_base_skill", rationale="load base first"),
        dict(action="dep_child_skill", rationale="then child"),
        dict(action="stop", rationale="", value="done"),
    ]
    agent = Agent(
        interaction_mode=None,
        name="skill_dep_ok",
        tools=[tool_a, tool_b, stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(script),
        skills=[base, child],
        initial_messages=None,
    )
    out, _ = agent.invoke()
    assert out.value == "done"


def test_skill_depends_on_rejects_missing_prerequisite():
    """Loading a skill with depends_on raises if the dependency has not been loaded yet."""
    base = Skill(
        name="dep_base_skill",
        description="base",
        instructions="base instructions",
        tools=[tool_a],
    )
    child = Skill(
        name="dep_child_skill",
        description="child",
        instructions="child instructions",
        tools=[tool_b],
        depends_on=base,
    )
    script = [dict(action="dep_child_skill", rationale="skip base")]
    agent = Agent(
        interaction_mode=None,
        name="skill_dep_bad",
        tools=[tool_a, tool_b, stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(script),
        skills=[base, child],
        initial_messages=None,
    )
    with pytest.raises(RuntimeError, match="has not been loaded"):
        agent.invoke()


def test_auto_loaded_skill_dependency_order():
    """Auto-loaded skills must respect dependency order."""
    base = Skill(
        name="dep_auto_base_skill",
        description="base",
        instructions="base instructions",
        tools=[tool_a],
    )
    child = Skill(
        name="dep_auto_child_skill",
        description="child",
        instructions="child instructions",
        tools=[tool_b],
        depends_on=base,
    )

    ok_agent = Agent(
        interaction_mode=None,
        name="auto_dep_ok",
        tools=[tool_a, tool_b, stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="done")]
        ),
        auto_loaded_skills=[base, child],
        initial_messages=None,
    )
    out, _ = ok_agent.invoke()
    assert out.value == "done"

    bad_agent = Agent(
        interaction_mode=None,
        name="auto_dep_bad",
        tools=[tool_a, tool_b, stop],
        system_prompt="prompt",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="done")]
        ),
        auto_loaded_skills=[child, base],
        initial_messages=None,
    )
    with pytest.raises(RuntimeError, match="has not been loaded"):
        bad_agent.invoke()


def test_agent_info(capsys: pytest.CaptureFixture[str]):
    tools = [tool_a, tool_b, tool_c, stop]
    skills = [skill]
    default_tool2 = default_tool.copy(name="other_default")
    agent = Agent(
        interaction_mode=None,
        name="test_agent_info",
        tools=tools,
        system_prompt="system_prompt for a testing agent",
        skills=skills,
        auto_loaded_skills=[auto_skill],
        default_tools=[default_tool, default_tool2],
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="unused")]
        ),
        initial_messages=None,
    )
    agent.initial_messages = ["This is a test initial User message!"]
    agent.show_agent_info()
    output = capsys.readouterr().out
    assert "Tools" in output
    assert "tool_a" in output
