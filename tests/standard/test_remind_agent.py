"""Tests for remind_agent interval and prior-reminder detection."""

import json
from typing import Any

import pytest

from roboz.agent import Agent
from roboz.tools import stop
from roboz.models import Empty, Message, Str
from roboz.models import Role
from roboz import tool
from roboz.llm import MockLLMEndpoint
from roboz.standard.tools.agent_runtime import RemindCtx, Reminder, remind_agent

CTX = RemindCtx(interval=3, message="nudge text", flag="test_flag")


def _user_reminder_record(value: str, flag: str) -> Message:
    return Message(
        role=Role.USER,
        content=json.dumps({"caller": "remind_agent", "value": value, "flag": flag}),
    )


def _make_tool():
    return remind_agent(CTX)


def test_remind_agent_insufficient_assistant_depth_returns_skip_str():
    messages = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="hi"),
        Message(role=Role.ASSISTANT, content='{"action":"x","rationale":""}'),
        Message(role=Role.USER, content="tool1"),
        Message(role=Role.ASSISTANT, content='{"action":"x","rationale":""}'),
        Message(role=Role.USER, content="tool2"),
    ]
    out = _make_tool()(input=Empty(), messages=messages)
    assert isinstance(out, Str)
    assert out.value == "No need for reminder"


def test_remind_agent_fires_reminder_after_interval_assistants():
    messages = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="hi"),
        Message(role=Role.ASSISTANT, content="a1"),
        Message(role=Role.USER, content="t1"),
        Message(role=Role.ASSISTANT, content="a2"),
        Message(role=Role.USER, content="t2"),
        Message(role=Role.ASSISTANT, content="a3"),
        Message(role=Role.USER, content="t3"),
    ]
    out = _make_tool()(input=Empty(), messages=messages)
    assert isinstance(out, Reminder)
    assert out.value == CTX.message
    assert out.flag == CTX.flag


def test_remind_agent_skips_when_matching_reminder_in_window():
    messages = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="hi"),
        Message(role=Role.ASSISTANT, content="a1"),
        Message(role=Role.USER, content="t1"),
        Message(role=Role.ASSISTANT, content="a2"),
        _user_reminder_record("prior", CTX.flag),
        Message(role=Role.ASSISTANT, content="a3"),
        Message(role=Role.USER, content="t3"),
    ]
    out = _make_tool()(input=Empty(), messages=messages)
    assert isinstance(out, Str)
    assert out.value == "No need for reminder"


@tool
def noop_entry(input: Empty, messages: list[Message]) -> Str:
    return Str(value="ok")


@pytest.mark.parametrize("interval,expected_reminder_count", [(2, 1), (3, 0)])
def test_remind_agent_default_tool_integration(
    interval: int, expected_reminder_count: int
):
    """With interval=2, one Reminder user record after two LLM turns; interval=3, none before stop."""
    flag = "integration_flag"
    remind = remind_agent(RemindCtx(interval=interval, message="ping", flag=flag))
    scripted: list[dict[str, Any] | Exception] = [
        dict(action="noop_entry", rationale=""),
        dict(action="noop_entry", rationale=""),
        dict(action="stop", rationale="", value="done"),
    ]
    agent = Agent(
        interaction_mode=None,
        name="remind_agent_test",
        tools=[noop_entry, stop],
        system_prompt="test",
        default_tools=[remind],
        agent_endpoint=MockLLMEndpoint(scripted),
        initial_messages=None,
    )
    _, messages = agent.invoke()

    reminder_hits = [
        m
        for m in messages
        if m.role == Role.USER and flag in m.content and '"value": "ping"' in m.content
    ]
    assert len(reminder_hits) == expected_reminder_count
