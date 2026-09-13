"""Investigate TODO: "When skill is invoked the default tools do not fire properly".

The existing ``test_calling`` proves the default tool fires once per agentic call
across skill loads, but it only ever re-invokes a skill that was already an active
tool from the start. It never exercises the case that would actually break if the
rebuilt master tool were stale: invoking a tool that becomes callable *only because
a skill was loaded*.

These tests close that gap and pin the exact firing order.
"""

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Message, Str
from roboz.skill.core import Skill
from roboz.tooling.decorators import factory, tool
from roboz.tools import stop


def _default_tool(log: list[str]):
    @factory
    def dflt(input: Empty, messages: list[Message], ctx: list[str]) -> Empty:
        ctx.append("dflt")
        return Empty()

    return dflt(log)


def _recording_tool(name: str, log: list[str]):
    @tool
    def recording_tool(input: Empty, messages: list[Message]) -> Str:
        log.append(name)
        return Str(value=name)

    recording_tool.name = name
    return recording_tool


def test_default_tool_fires_before_calling_a_skill_only_tool():
    """A tool that only becomes active after a skill loads must be callable, and
    the default tool must still fire before the agentic call that invokes it."""
    log: list[str] = []

    alpha = _recording_tool("alpha", log)
    # `beta` is NOT a base tool; it only becomes active once the skill is loaded.
    beta = _recording_tool("beta", log)

    skill = Skill(
        name="load_beta",
        description="makes beta available",
        instructions="use beta",
        tools=[beta],
    )

    script = [
        dict(action="alpha", rationale=""),
        dict(action="load_beta", rationale="load the skill"),
        dict(action="beta", rationale="now use the skill-only tool"),
        dict(action="stop", rationale="", value="done"),
    ]

    agent = Agent(
        interaction_mode=None,
        name="skill_only_tool_agent",
        tools=[alpha, stop],
        system_prompt="prompt",
        default_tools=[_default_tool(log)],
        agent_endpoint=MockLLMEndpoint(script),
        skills=[skill],
        initial_messages=None,
    )

    out, _ = agent.invoke()

    assert out.value == "done"
    # The skill-only tool actually ran -> the master tool was rebuilt correctly.
    assert "beta" in log
    # One default-tool firing before each of the 4 agentic (LLM) calls.
    assert log.count("dflt") == 4
    # Exact interleaving: every recorded active-tool run is immediately preceded by
    # a default-tool firing. The skill-load step uses the auto-generated skill tool
    # and the final `stop` is the library tool; neither records, so they appear only
    # as the extra leading/trailing "dflt" entries.
    assert log == ["dflt", "alpha", "dflt", "dflt", "beta", "dflt"]


def test_two_default_tools_fire_in_order_after_skill_load():
    """With multiple default tools they must all fire, in order, before the agentic
    call that follows a skill load."""
    log: list[str] = []

    beta = _recording_tool("beta", log)
    skill = Skill(
        name="load_beta2",
        description="makes beta available",
        instructions="use beta",
        tools=[beta],
    )

    @factory
    def d1(input: Empty, messages: list[Message], ctx: list[str]) -> Empty:
        ctx.append("d1")
        return Empty()

    @factory
    def d2(input: Empty, messages: list[Message], ctx: list[str]) -> Empty:
        ctx.append("d2")
        return Empty()

    script = [
        dict(action="load_beta2", rationale="load the skill"),
        dict(action="beta", rationale="use skill-only tool"),
        dict(action="stop", rationale="", value="ok"),
    ]

    agent = Agent(
        interaction_mode=None,
        name="two_default_skill_agent",
        tools=[stop],
        system_prompt="prompt",
        default_tools=[d1(log), d2(log)],
        agent_endpoint=MockLLMEndpoint(script),
        skills=[skill],
        initial_messages=None,
    )

    out, _ = agent.invoke()

    assert out.value == "ok"
    assert "beta" in log
    # 3 agentic calls -> both default tools fire 3 times each, always d1 before d2.
    assert log.count("d1") == 3
    assert log.count("d2") == 3
    # `stop` is the library tool and does not record; it is preceded by the final
    # d1/d2 pair.
    assert log == ["d1", "d2", "d1", "d2", "beta", "d1", "d2"]
