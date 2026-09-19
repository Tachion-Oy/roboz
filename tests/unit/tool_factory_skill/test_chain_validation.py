import pytest

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Int, Invoke, Message, Stop, Str, Strs
from roboz.tooling.decorators import factory, tool

# Default real endpoint loads API keys; tests only validate tool chains at init.
_MOCK_AGENT_ENDPOINT = MockLLMEndpoint(
    [dict(action="stop", rationale="", value="unused")]
)

# Agentic flows require a non-empty system prompt; these tests only care about
# tool-chain validation, so any non-empty prompt suffices.
_SYSTEM_PROMPT = "chain validation agent"


def test_tool_chain_valid():
    @tool
    def root_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @tool(chained_to=root_tool)
    def next_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=str(int(input.value) + 1))

    assert next_tool.chained_to == [root_tool]

    # Chain validation happens at Agent init time.
    Agent(
        name="chain_validation",
        tools=[root_tool, next_tool],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )

    # Should not fail according to Liskov: upstream output `Scalar` is-a `Empty`.
    @tool(chained_to=root_tool)
    def liskov_tool(input: Empty, messages: list[Message]) -> Str:
        return Str(value="0")

    Agent(
        name="chain_validation",
        tools=[root_tool, liskov_tool],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )


def test_tool_chain_invalid_type():
    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    # Referenced chained tool must exist in the agent tool set.
    @tool
    def root_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @tool(chained_to=root_tool)
    def next_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=str(int(input.value) + 1))

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[next_tool],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )

    # Chained tool may not return Invoke (must be an Empty subclass).
    @tool
    def dynamic_tool(input: Str, messages: list[Message]) -> Invoke:
        return Invoke(action="x", rationale="")

    @tool(chained_to=dynamic_tool)
    def chained_from_invoke(input: Empty, messages: list[Message]) -> Str:
        return Str(value="0")

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[dynamic_tool, chained_from_invoke],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_tool_chain_list_valid():
    @tool
    def valid_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @tool(chained_to=[valid_tool, valid_tool])
    def next_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=str(int(input.value) + 1))

    assert next_tool.chained_to is not None
    assert len(next_tool.chained_to) == 2

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    Agent(
        name="chain_validation",
        tools=[valid_tool, next_tool],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )


def test_tool_chain_list_invalid():
    @tool
    def invalid_tool(input: Str, messages: list[Message]) -> Empty:
        return Empty()

    @tool
    def valid_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @tool(chained_to=[valid_tool, invalid_tool])
    def next_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[valid_tool, invalid_tool, next_tool],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_factory_chain_valid():

    @factory
    def root_factory(input: Str, messages: list[Message], ctx: None) -> Str:
        return Str(value=input.value)

    # NOTE: `chained_to` references the Factory, not the resolved Tool.
    @factory(chained_to=root_factory)
    def next_factory(input: Str, messages: list[Message], ctx: None) -> Str:
        return Str(value=str(int(input.value) + 1))

    ctx = None
    root_tool = root_factory(ctx)
    next_tool = next_factory(ctx)

    assert next_tool.chained_to == [root_factory]

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    # Validation resolves by name at Agent init time.
    Agent(
        name="chain_validation",
        tools=[root_tool, next_tool],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )


def test_factory_chain_list_invalid():

    @tool
    def invalid_tool(input: Str, messages: list[Message]) -> Empty:
        return Empty()

    @tool
    def valid_tool(input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @factory(chained_to=[valid_tool, invalid_tool])
    def next_factory(input: Str, messages: list[Message], ctx: None) -> Str:
        return Str(value="1")

    next_tool = next_factory(None)

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[valid_tool, invalid_tool, next_tool],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_tool_chain_fork_valid():
    """Fork: parent outputs union type, children accept constituents. Must type-check."""

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
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    Agent(
        name="chain_validation",
        tools=[tool_d, tool_e, tool_f],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )
    assert tool_e.chained_to == [tool_d]
    assert tool_f.chained_to == [tool_d]


def test_tool_chain_fork_invalid_child_input_not_in_union():
    """Child accepts Strs; parent outputs Int|Str. Neither constituent is subclass of Strs."""

    @tool
    def tool_d(input: Int, messages: list[Message]) -> Int | Str:
        return Int(value=input.value)

    @tool(chained_to=tool_d, chain_condition=lambda x: True)
    def tool_e(input: Strs, messages: list[Message]) -> Strs:
        return Strs(items=[])

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[tool_d, tool_e],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_tool_chain_fork_invalid_both_children_same_wrong_type():
    """Parent outputs Int|Str; both children accept Strs. No constituent matches."""

    @tool
    def tool_d(input: Int, messages: list[Message]) -> Int | Str:
        return Int(value=input.value)

    @tool(chained_to=tool_d, chain_condition=lambda x: True)
    def tool_e(input: Strs, messages: list[Message]) -> Strs:
        return Strs(items=[])

    @tool(chained_to=tool_d, chain_condition=lambda x: False)
    def tool_f(input: Strs, messages: list[Message]) -> Strs:
        return Strs(items=[])

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[tool_d, tool_e, tool_f],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_tool_chain_fork_invalid_union_parent_child_expects_int_only():
    """Parent outputs Str|Strs; child accepts Int. Neither Str nor Strs is subclass of Int."""

    @tool
    def tool_d(input: Int, messages: list[Message]) -> Str | Strs:
        return Str(value="x")

    @tool(chained_to=tool_d)
    def tool_e(input: Int, messages: list[Message]) -> Int:
        return Int(value=0)

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    with pytest.raises(ValueError):
        Agent(
            name="chain_validation",
            tools=[tool_d, tool_e],
            agent_endpoint=_MOCK_AGENT_ENDPOINT,
            system_prompt=_SYSTEM_PROMPT,
            initial_messages=None,
        )


def test_tool_chain_fork_valid_parent_outputs_stop():
    """Parent outputs Stop only; Stop is filtered out, no constituents to validate. Chain never activates."""

    @tool
    def tool_d(input: Int, messages: list[Message]) -> Stop:
        return Stop(value="done")

    @tool(chained_to=tool_d)  # OK for Pylance to flag as this is redundant
    def tool_e(input: Empty, messages: list[Message]) -> Str:
        return Str(value="ok")

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    Agent(
        name="chain_validation",
        tools=[tool_d, tool_e],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )


def test_tool_chain_fork_valid_parent_outputs_union_with_stop():
    """Parent outputs Int | Stop; Stop is filtered out, Int matches child input. Valid."""

    @tool
    def tool_d(input: Int, messages: list[Message]) -> Int | Stop:
        return Int(value=0)

    @tool(chained_to=tool_d, chain_condition=lambda x: True)
    def tool_e(input: Int, messages: list[Message]) -> Int:
        return Int(value=0)

    @tool
    def default_tool(input: Empty, messages: list[Message]) -> Empty:
        return Empty()

    Agent(
        name="chain_validation",
        tools=[tool_d, tool_e],
        agent_endpoint=_MOCK_AGENT_ENDPOINT,
        system_prompt=_SYSTEM_PROMPT,
        initial_messages=None,
    )
