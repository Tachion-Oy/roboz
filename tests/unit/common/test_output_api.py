"""Tests for :class:`~roboz.runtime.Output.API` interaction seam."""

import json

import pytest

from roboz.agent import get_active_agent_stack
from roboz.agent.core import Agent, AgentMode
from roboz.exceptions import UserInputUnavailableError
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Message, Role, Stop, Str
from roboz.runtime import (
    Output,
    bind_output,
    interact_with_user,
    reset_output,
    run_cancellable_external_call,
)
from roboz.runtime import io as utils
from roboz.tooling.decorators import tool
from roboz.tools import prompt_user_at_start, stop


def test_interact_with_user_requires_bound_output():
    with pytest.raises(UserInputUnavailableError, match="interaction is unavailable"):
        interact_with_user("q?", True)


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


def test_unavailable_output_binding_masks_api_adapter(bind_user_io):
    bind_user_io([])
    token = bind_output(None)
    try:
        assert utils.get_bound_output() is None
        assert utils.get_bound_output(default=Output.CLI) is Output.CLI
    finally:
        reset_output(token)

    assert utils.get_bound_output() is Output.API


@pytest.mark.parametrize("output", list(Output))
def test_output_getter_preserves_explicit_channel(output, bind_user_io):
    bind_user_io([])
    token = bind_output(output)
    try:
        assert utils.get_bound_output(default=Output.CLI) is output
    finally:
        reset_output(token)


def test_missing_sidecar_precedes_cli_output_and_read(monkeypatch, capsys):
    monkeypatch.setattr(
        utils.sys.stdin,
        "readline",
        lambda: (_ for _ in ()).throw(AssertionError("must not read input")),
    )
    output_token = bind_output(Output.CLI)
    autonomous_token = bind_output(None)
    try:
        with pytest.raises(UserInputUnavailableError):
            interact_with_user("hidden question", True)
    finally:
        reset_output(autonomous_token)
        reset_output(output_token)

    assert capsys.readouterr().out == ""


def test_missing_sidecar_rejects_all_direct_interaction_and_restores_context(
    bind_user_io,
):
    io = bind_user_io(["answer"])
    token = bind_output(None)
    try:
        with pytest.raises(UserInputUnavailableError):
            interact_with_user("status", False)
        with pytest.raises(UserInputUnavailableError):
            interact_with_user("question", True)
    finally:
        reset_output(token)

    assert interact_with_user("question", True) == "answer"
    assert io.prompts == ["question"]


def test_external_call_worker_has_no_direct_interaction_sidecar(bind_user_io):
    io = bind_user_io([])
    with pytest.raises(UserInputUnavailableError):
        run_cancellable_external_call(lambda: interact_with_user("question", True))

    assert io.prompts == []


def test_autonomous_sidecar_mask_resets_when_finalization_fails(
    bind_user_io, monkeypatch
):
    outer_stack = get_active_agent_stack()
    io = bind_user_io(["after failure"])
    agent = Agent(
        name="failing_finalization",
        mode=AgentMode.AUTONOMOUS,
        tools=[stop],
        system_prompt="Stop immediately.",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "ok"}]
        ),
    )

    def fail_finalization(*, status):
        raise RuntimeError(f"finalization failed: {status}")

    monkeypatch.setattr(agent.pipe, "finalize_run", fail_finalization)

    with pytest.raises(RuntimeError, match="finalization failed"):
        agent.invoke()

    assert get_active_agent_stack() == outer_stack
    assert interact_with_user("still available", True) == "after failure"
    assert io.prompts == ["still available"]


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
