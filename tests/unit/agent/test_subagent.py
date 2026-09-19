"""Tests for roboz.agent.subagent."""

from unittest.mock import patch

from roboz.agent.core import Agent
from roboz.agent.subagent import SUBAGENT_NO_OUTCOME_PLACEHOLDER, run_subagent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Stop, Str
from roboz.tools import stop


def test_run_subagent_returns_child_stop_value() -> None:
    child = Agent(
        name="subagent_child",
        tools=[stop],
        system_prompt="sub",
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "stop",
                    "rationale": "done",
                    "value": "plan is in ./plans/foo",
                }
            ]
        ),
    )
    tool = run_subagent(child)
    out = tool(input=Empty(), messages=[])
    assert out.value == "plan is in ./plans/foo"


def test_run_subagent_uses_placeholder_when_stop_has_no_value() -> None:
    child = Agent(
        name="subagent_placeholder",
        tools=[stop],
        system_prompt="sub",
        agent_endpoint=MockLLMEndpoint([]),
    )
    tool = run_subagent(child)
    with patch.object(child, "invoke", return_value=(Stop(value=None), [])):
        out = tool(input=Empty(), messages=[])
    assert out.value == SUBAGENT_NO_OUTCOME_PLACEHOLDER


def test_run_subagent_passes_input_to_child_agent() -> None:
    child = Agent(
        name="subagent_input",
        tools=[stop],
        system_prompt="sub",
        agent_endpoint=MockLLMEndpoint([]),
    )
    tool = run_subagent(child)
    payload = Str(value="input for the child")

    with patch.object(child, "invoke", return_value=(Stop(value="done"), [])) as invoke:
        out = tool.caller(input=payload, messages=[])

    invoke.assert_called_once()
    assert invoke.call_args.kwargs["input"] is payload
    assert out.value == "done"
