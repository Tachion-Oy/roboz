import json
import logging

import pytest
from pydantic import ValidationError

from roboz.agent.background_agent import run_background_agent
from roboz.agent.core import Agent
from roboz.agent.prompt_agent_tool import prompt_agent
from roboz.tools import (
    MessageCtx,
    PromptUser,
    PromptUserCtx,
    message_user,
    prompt_user,
    prompt_user_at_start,
    stop,
)
from roboz.agent.subagent import run_subagent
from roboz.runtime import io as utils
from roboz.models import Empty, Message, Stop, Str
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime import Output
from roboz.models import Role
from roboz.agent._identifiers import (
    PROMPT_AGENT_TOOL_NAME,
    RUN_BACKGROUND_AGENT_TOOL_NAME,
    RUN_SUBAGENT_TOOL_NAME,
)
from roboz.tools._identifiers import (
    MESSAGE_USER_TOOL_NAME,
    PROMPT_USER_AT_START_TOOL_NAME,
    PROMPT_USER_TOOL_NAME,
    STOP_TOOL_NAME,
)
from roboz.tooling.decorators import tool
from roboz.llm.endpoints import MockLLMEndpoint


def test_prompt_user_at_start_skips_if_assistant_present():
    tool = prompt_user_at_start(MessageCtx(message="m"))
    result = tool(
        input=Str(value="prompt text"),
        messages=[Message(role=Role.ASSISTANT, content="{}")],
    )
    assert result.value == ""
    assert result.truncation == NO_MESSAGE


def test_prompt_user_at_start_returns_interaction_reply(bind_user_io):
    tool = prompt_user_at_start(MessageCtx(message="m"))
    io = bind_user_io(["from user"])
    out = tool(input=Str(value="question"), messages=[])
    assert out.value == "from user"
    assert io.prompts == ["m"]


@pytest.mark.parametrize(
    "tool_obj, identifier",
    [
        (prompt_agent, PROMPT_AGENT_TOOL_NAME),
        (prompt_user, PROMPT_USER_TOOL_NAME),
        (message_user, MESSAGE_USER_TOOL_NAME),
        (prompt_user_at_start, PROMPT_USER_AT_START_TOOL_NAME),
        (stop, STOP_TOOL_NAME),
        (run_background_agent, RUN_BACKGROUND_AGENT_TOOL_NAME),
        (run_subagent, RUN_SUBAGENT_TOOL_NAME),
    ],
)
def test_tool_name_matches_identifier(tool_obj, identifier):
    """Each centralized identifier must match the real tool/factory name."""
    assert tool_obj.name == identifier


def test_prompt_agent_does_not_emit_redundant_calling_llm_log(caplog):
    caplog.set_level(logging.INFO, logger="roboz.agent.prompt_agent_tool")
    agent = Agent(
        interaction_mode=Output.API,
        name="logger_agent",
        tools=[stop],
        system_prompt="Stop immediately.",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
    )

    agent.invoke()

    assert not any(
        record.name == "roboz.agent.prompt_agent_tool" for record in caplog.records
    )


def test_prompt_user_returns_reply(bind_user_io):
    tool = prompt_user(PromptUserCtx(timeout_reply="continuing"))
    io = bind_user_io(["hi"])
    out = tool(input=PromptUser(value="q?"), messages=[])
    assert out.value == "hi"
    assert io.prompts == ["q?"]
    assert io.timeouts == [None]


def test_prompt_user_forwards_timeout(bind_user_io):
    tool = prompt_user(PromptUserCtx(timeout_reply="continuing"))
    io = bind_user_io(["hi"])
    out = tool(input=PromptUser(value="q?", timeout_seconds=0.5), messages=[])
    assert out.value == "hi"
    assert io.timeouts == [0.5]


def test_prompt_user_uses_timeout_reply_when_no_response(bind_user_io):
    tool = prompt_user(PromptUserCtx(timeout_reply="continuing"))
    bind_user_io([None])
    out = tool(input=PromptUser(value="q?", timeout_seconds=0.01), messages=[])
    assert out.value == "continuing"


def test_prompt_user_timeout_seconds_must_be_positive():
    with pytest.raises(ValidationError):
        PromptUser(value="q?", timeout_seconds=0)


def test_prompt_user_timeout_seconds_defaults_to_none():
    assert PromptUser(value="q?").timeout_seconds is None


def test_prompt_user_at_start_as_default_tool_runs_before_first_assistant_message(
    monkeypatch,
):

    @tool
    def entry(input: Empty, messages: list[Message]) -> Str:
        return Str(value="question")

    endpoint = MockLLMEndpoint(
        responses=[
            {
                "action": "entry",
                "rationale": "produce question for default tool",
            },
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    start_only = prompt_user_at_start(MessageCtx(message="m"))
    agent = Agent(
        interaction_mode=Output.CLI,
        name="ask_default_tool",
        tools=[entry, stop],
        system_prompt="Test default tool flow.",
        default_tools=[start_only],
        custom_prompt_user_tool=None,
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    reads: list[str] = []

    def _readline() -> str:
        reads.append("from user\n")
        return "from user\n"

    monkeypatch.setattr(utils.sys.stdin, "readline", _readline)
    output, messages = agent.invoke()
    assert len(reads) == 1

    assert isinstance(output, Stop)
    assert output.value == "ok"

    user_payloads: list[dict] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            user_payloads.append(json.loads(message.content))
        except json.JSONDecodeError:
            continue

    ask_results = [
        payload
        for payload in user_payloads
        if payload.get("caller") == "prompt_user_at_start"
    ]
    assert len(ask_results) == 2

    non_empty_ask_results = [p for p in ask_results if p.get("value")]
    assert len(non_empty_ask_results) == 1
    assert non_empty_ask_results[0].get("value") == "from user\n"
