"""Tests for roboz.agent.subagents."""

import json
from pathlib import Path
from threading import Event

import pytest

from roboz.agent import get_active_agent_stack
from roboz.agent.background_agent import BackgroundAgentContext
from roboz.agent.core import Agent, AgentMode
from roboz.agent.subagents import (
    SharedNotes,
    SubagentGoals,
    SubagentsContext,
    read_shared_notes,
    start_subagents,
)
from roboz.llm import MockLLMEndpoint
from roboz.llm.endpoints import LLMEndpointRoute
from roboz.models import Empty, Message, Stop, Str, filter_messages, render_content
from roboz.runtime import EventPipe, default_event_sinks, run_cancellable_external_call
from roboz.skill import Skill
from roboz.tooling import Tool, tool

STOP = {"action": "stop", "rationale": "done", "value": "result"}
BLOCK = {"action": "block", "rationale": "wait"}
GOALS = SubagentGoals(task="Find shows", subgoals=["true crime", "documentaries"])
ONE_GOAL = SubagentGoals(task="Find shows", subgoals=["true crime"])


@tool
def finish(input: Empty, messages: list[Message]) -> Stop:
    return Stop()


def _team(tools: list[Tool], skills: list[Skill] | None = None) -> SubagentsContext:
    return SubagentsContext(
        name="searcher", tools=tools, skills=skills or [], pipe=EventPipe()
    )


def _join(team: list[BackgroundAgentContext]) -> None:
    for member in team:
        assert member.state.thread is not None
        member.state.thread.join(timeout=2)


def _delegate(
    team: SubagentsContext, endpoint: MockLLMEndpoint | LLMEndpointRoute
) -> tuple[list[str], list[str]]:
    """Start two subagents from a scripted parent; return its start report and notes."""

    @tool
    def give_goals(input: Empty, messages: list[Message]) -> SubagentGoals:
        return GOALS

    @tool
    def wait_for_team(input: Empty, messages: list[Message]) -> Empty:
        _join(team.team)
        return Empty()

    parent = Agent(
        name="parent",
        mode=AgentMode.DETERMINISTIC,
        agent_endpoint=endpoint,
        default_tools=[give_goals, wait_for_team, read_shared_notes(team), finish],
        tools=[start_subagents(team).copy(chained_to=give_goals)],
        event_pipe=team.pipe,
    )
    _, messages = parent.invoke()
    [started] = filter_messages(messages, caller="start_subagents")
    [notes] = filter_messages(messages, caller="read_shared_notes")
    return json.loads(started.content)["items"], json.loads(notes.content)["items"]


def test_notes_reach_every_other_reader_once() -> None:
    notes = SharedNotes()
    notes.post("a", "found a source")

    assert notes.unread("b") == ["a: found a source"]
    assert notes.unread("b") == []
    assert notes.unread("a") == []


def test_subagents_are_named_briefed_and_report_through_the_notes() -> None:
    @tool
    def web_search(input: Str, messages: list[Message]) -> Str:
        return Str(value="")

    @tool
    def read_page(input: Str, messages: list[Message]) -> Str:
        return Str(value="")

    reading = Skill(
        name="reading",
        description="Read pages.",
        instructions="Cite every source.",
        tools=[read_page],
    )
    team = _team([web_search], [reading])

    started, notes = _delegate(team, MockLLMEndpoint([STOP, STOP]))

    assert started == ["searcher_1: true crime", "searcher_2: documentaries"]
    assert sorted(notes) == [
        "searcher_1: result",
        "searcher_1: stopped (completed)",
        "searcher_2: result",
        "searcher_2: stopped (completed)",
    ]
    for member, goal in zip(team.team, GOALS.subgoals):
        agent = member.agent
        assert agent.default_tools == []
        assert {t.name for t in agent.active_tools.values()} == {
            "web_search",
            "share_note",
            "read_shared_notes",
            "stop",
            "reading",
            "read_page",
        }
        [loaded] = filter_messages(agent.messages, caller="reading")
        assert "Cite every source." in render_content(loaded)
        prompt = render_content(agent.messages[0])
        assert f"You are {agent.name}," in prompt
        assert "Common task: Find shows" in prompt
        assert f"Your sub-goal: {goal}" in prompt
        assert "searcher_1: true crime\nsearcher_2: documentaries" in prompt
        assert "### `web_search`" in prompt


def test_reading_with_nothing_new_still_answers() -> None:
    team = _team([])

    _, messages = Agent(
        name="parent",
        mode=AgentMode.DETERMINISTIC,
        agent_endpoint=None,
        default_tools=[read_shared_notes(team), finish],
        event_pipe=team.pipe,
    ).invoke()

    [read] = filter_messages(messages, caller="read_shared_notes")
    assert json.loads(read.content)["items"] == ["No new notes."]


def test_failed_subagent_is_reported_while_the_other_finishes() -> None:
    _, notes = _delegate(_team([]), MockLLMEndpoint([RuntimeError("boom"), STOP]))

    assert sorted(note.split(": ", 1)[1] for note in notes) == [
        "result",
        "stopped (completed)",
        "stopped (failed)",
    ]


def test_subagents_that_do_not_start_are_reported_to_the_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def select_endpoint() -> MockLLMEndpoint:
        raise LookupError("no model selected")

    monkeypatch.setattr("roboz.agent.background_agent.BACKGROUND_START_TIMEOUT_S", 0.01)

    started, notes = _delegate(
        _team([]), LLMEndpointRoute(get_endpoint=select_endpoint)
    )

    assert started == ["searcher_1: did not start", "searcher_2: did not start"]
    assert sorted(notes) == [
        "searcher_1: stopped (failed)",
        "searcher_2: stopped (failed)",
    ]


def test_subagents_save_their_conversations_next_to_their_owner(
    tmp_path: Path,
) -> None:
    owner_sinks = default_event_sinks(data_path=tmp_path / "parent", include_cli=False)
    team = SubagentsContext(name="searcher", pipe=EventPipe(event_sinks=owner_sinks))

    _delegate(team, MockLLMEndpoint([STOP, STOP]))

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "parent",
        "searcher_1",
        "searcher_2",
    ]
    assert all(any(folder.rglob("*.json")) for folder in tmp_path.iterdir())


def test_starting_again_cancels_the_running_team() -> None:
    entered, release = Event(), Event()
    first_team: list[BackgroundAgentContext] = []
    first_notes: list[SharedNotes] = []

    @tool
    def block(input: Empty, messages: list[Message]) -> Str:
        entered.set()
        release.wait(timeout=2)
        return Str(value="")

    team = _team([block])

    @tool
    def give_goal(input: Empty, messages: list[Message]) -> SubagentGoals:
        return ONE_GOAL

    @tool
    def give_goal_again(input: Empty, messages: list[Message]) -> SubagentGoals:
        assert entered.wait(timeout=2)
        first_team.extend(team.team)
        first_notes.append(team.notes)
        return ONE_GOAL

    @tool
    def release_and_finish(input: Empty, messages: list[Message]) -> Stop:
        release.set()
        _join([*first_team, *team.team])
        return Stop()

    Agent(
        name="parent",
        mode=AgentMode.DETERMINISTIC,
        agent_endpoint=MockLLMEndpoint([BLOCK, STOP]),
        default_tools=[give_goal, give_goal_again, release_and_finish],
        tools=[start_subagents(team).copy(chained_to=[give_goal, give_goal_again])],
        event_pipe=team.pipe,
    ).invoke()

    assert first_notes[0].unread("observer")[-1] == "searcher_1: stopped (cancelled)"
    assert team.notes.unread("observer")[-1] == "searcher_1: stopped (completed)"


def test_cancelling_the_owner_abandons_a_subagent_call_in_flight() -> None:
    entered, release = Event(), Event()
    owner_pipe = EventPipe()

    # Bound to the owner's pipe, as a capability binds the tools it builds.
    @tool
    def block(input: Empty, messages: list[Message]) -> Str:
        def call_a_slow_service() -> bool:
            entered.set()
            return release.wait(timeout=5)

        run_cancellable_external_call(
            call_a_slow_service, control_signals=owner_pipe.control_signals
        )
        return Str(value="")

    team = SubagentsContext(name="searcher", tools=[block], pipe=owner_pipe)

    @tool
    def give_goal(input: Empty, messages: list[Message]) -> SubagentGoals:
        return ONE_GOAL

    @tool
    def cancel_owner(input: Empty, messages: list[Message]) -> Empty:
        assert entered.wait(timeout=2)
        team.pipe.cancel()
        return Empty()

    Agent(
        name="parent",
        mode=AgentMode.DETERMINISTIC,
        agent_endpoint=MockLLMEndpoint([BLOCK]),
        default_tools=[give_goal, cancel_owner],
        tools=[start_subagents(team).copy(chained_to=give_goal)],
        event_pipe=team.pipe,
    ).invoke()
    _join(team.team)
    stopped_while_the_call_hung = not release.is_set()
    release.set()

    assert stopped_while_the_call_hung
    assert team.notes.unread("observer") == ["searcher_1: stopped (cancelled)"]
    assert not team.team[0].state.thread.is_alive()


def test_a_finished_subagent_does_not_stop_its_teammate() -> None:
    entered, release = Event(), Event()
    working: list[str] = []
    teammate_cancelled: list[bool] = []

    @tool
    def block(input: Empty, messages: list[Message]) -> Str:
        working.append(get_active_agent_stack()[-1])
        entered.set()
        release.wait(timeout=2)
        return Str(value="")

    team = _team([block])

    @tool
    def give_goals(input: Empty, messages: list[Message]) -> SubagentGoals:
        return GOALS

    @tool
    def watch_teammate(input: Empty, messages: list[Message]) -> Stop:
        assert entered.wait(timeout=2)
        members = {member.agent.name: member for member in team.team}
        busy = members.pop(working[0])
        _join(list(members.values()))
        teammate_cancelled.append(busy.agent.pipe.cancelled)
        return Stop()

    Agent(
        name="parent",
        mode=AgentMode.DETERMINISTIC,
        agent_endpoint=MockLLMEndpoint([STOP, BLOCK]),
        default_tools=[give_goals, watch_teammate],
        tools=[start_subagents(team).copy(chained_to=give_goals)],
        event_pipe=team.pipe,
    ).invoke()
    release.set()
    _join(team.team)

    assert teammate_cancelled == [False]
