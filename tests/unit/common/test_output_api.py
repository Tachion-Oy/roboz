"""Tests for :class:`~roboz.runtime.Output.API` interaction seam."""

import json

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Message, Role, Stop, Str
from roboz.runtime import Output, bind_output, interact_with_user, reset_output
from roboz.runtime import io as utils
from roboz.tooling.decorators import tool
from roboz.tools import prompt_user_at_start, stop


def test_interact_with_user_requires_bound_output():
    try:
        interact_with_user("q?", True)
    except RuntimeError as e:
        assert "Runtime output is not bound" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_interact_with_user_cli_with_reply_reads_line(monkeypatch, capsys):
    monkeypatch.setattr(utils.sys.stdin, "readline", lambda: "typed\n")
    token = bind_output(Output.CLI)
    try:
        out = interact_with_user("q?", True)
    finally:
        reset_output(token)
    assert out == "typed\n"
    assert "q?" in capsys.readouterr().out


def test_read_line_with_timeout_reads_when_stdin_ready(monkeypatch):
    monkeypatch.setattr(
        utils.select, "select", lambda r, w, x, t: ([utils.sys.stdin], [], [])
    )
    monkeypatch.setattr(utils.sys.stdin, "readline", lambda: "typed\n")
    assert utils._read_line_with_timeout(5.0) == "typed\n"


def test_read_line_with_timeout_returns_none_when_stdin_not_ready(monkeypatch):
    monkeypatch.setattr(utils.select, "select", lambda r, w, x, t: ([], [], []))

    def _must_not_read() -> str:
        raise AssertionError("readline must not be called after a timeout")

    monkeypatch.setattr(utils.sys.stdin, "readline", _must_not_read)
    assert utils._read_line_with_timeout(0.01) is None


def test_read_line_with_timeout_none_timeout_skips_select(monkeypatch):
    def _must_not_select(*args, **kwargs):
        raise AssertionError("select must not be called when timeout is None")

    monkeypatch.setattr(utils.select, "select", _must_not_select)
    monkeypatch.setattr(utils.sys.stdin, "readline", lambda: "typed\n")
    assert utils._read_line_with_timeout(None) == "typed\n"


def test_interact_with_user_api_forwards_timeout_and_returns_none_on_timeout(
    bind_user_io,
):
    io = bind_user_io([None])
    assert interact_with_user("question?", True, timeout=2.5) is None
    assert io.prompts == ["question?"]
    assert io.timeouts == [2.5]


def test_interact_with_user_api_with_reply_requires_user_io():
    token = bind_output(Output.API)
    try:
        interact_with_user("q?", True)
    except RuntimeError as e:
        assert "roboz.runtime.bind_api_user_io" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
    finally:
        reset_output(token)


def test_interact_with_user_api_with_reply_uses_bound_user_io(bind_user_io):
    io = bind_user_io(["answer"])
    assert interact_with_user("question?", True) == "answer"
    assert io.prompts == ["question?"]


def test_interact_with_user_api_notify_uses_bound_user_io(bind_user_io):
    io = bind_user_io([])
    assert interact_with_user("hi", False) is None
    assert io.prompts == ["[notify]hi"]


def test_minimal_agent_output_api_terminates_via_user_io(bind_user_io):
    @tool
    def entry(input: Empty, messages: list[Message]) -> Str:
        return Str(value="question")

    endpoint = MockLLMEndpoint(
        responses=[
            {"action": "entry", "rationale": "ask"},
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    start_only = prompt_user_at_start("m")
    agent = Agent(
        interaction_mode=Output.API,
        name="api_output_agent",
        tools=[entry, stop],
        system_prompt="Test API output user I/O.",
        default_tools=[start_only],
        custom_prompt_user_tool=None,
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    io = bind_user_io(["from user"])
    output, messages = agent.invoke()

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
        p for p in user_payloads if p.get("caller") == "prompt_user_at_start"
    ]
    assert len(ask_results) == 2
    non_empty = [p for p in ask_results if p.get("value")]
    assert len(non_empty) == 1
    assert non_empty[0].get("value") == "from user"
    assert len(io.prompts) >= 1
