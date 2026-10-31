"""Tests for :class:`~roboz.runtime.Output.API` interaction seam."""

import json

import httpx
import pytest
from openai import OpenAI

from roboz.models import render_content
from roboz.agent import get_active_agent_stack
from roboz.agent.core import Agent, AgentMode
from roboz.exceptions import UserInputUnavailableError
from roboz.llm.endpoints import LLMEndpoint, MockLLMEndpoint
from roboz.models import Empty, Image, Message, Role, Stop, Str, StrImage
from roboz.runtime import (
    Output,
    bind_output,
    interact_with_user,
    reset_output,
    run_cancellable_external_call,
)
from roboz.runtime import io as utils
from roboz.tooling.decorators import tool
from roboz.tools import prompt_user, prompt_user_at_start, stop


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
    assert out == Str(value="typed\n")
    assert "q?" in capsys.readouterr().out


def test_interact_with_user_cli_timeout_remains_none(monkeypatch):
    monkeypatch.setattr(utils.select, "select", lambda r, w, x, t: ([], [], []))
    token = bind_output(Output.CLI)
    try:
        assert interact_with_user("Question?", True, timeout=0.01) is None
    finally:
        reset_output(token)


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
    assert io.image_capabilities == [False]


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
    reply = Str(value="answer")
    io = bind_user_io([reply])
    assert interact_with_user("question?", True) is reply
    assert io.prompts == ["question?"]


@pytest.mark.parametrize("images_first", [False, True])
def test_renamed_initial_and_followup_prompts_advertise_images_to_host(
    bind_user_io, images_first,
):
    images = [
        Image.from_bytes(b"first image", media_type="image/png"),
        Image.from_bytes(b"second image", media_type="image/jpeg"),
    ]
    text = Str(value="Text reply")
    image_only = StrImage(value="", images=[images[0]])
    replies = [image_only, text] if images_first else [text, image_only]
    replies.append(StrImage(value="Compare these files", images=images))
    io = bind_user_io(replies)
    actions = iter([
        {"action": "ask_again", "rationale": "Ask", "value": "Next?"},
        {"action": "ask_again", "rationale": "Compare", "value": "More?"},
        {"action": "stop", "rationale": "Done", "value": "Finished"},
    ])
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "typed-replies", "object": "chat.completion", "created": 0,
            "model": "vision", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(next(actions)),
                },
            }],
        })

    with OpenAI(
        api_key="test-only", base_url="https://provider.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        agent = Agent(
            name="typed_replies", system_prompt="Inspect the replies and stop.",
            tools=[stop],
            default_tools=[prompt_user_at_start("Start?").rename("begin")],
            custom_prompt_user_tool=prompt_user("No reply").rename("ask_again"),
            agent_endpoint=LLMEndpoint(
                client=client, api_name="test", model_name="vision",
                accepts_images=True, stream=False,
            ),
        )
        result, messages = agent.invoke()

    assert result.value == "Finished"
    assert io.prompts == ["Start?", "Next?", "More?"]
    assert io.image_capabilities == [True, True, True]
    assert len(requests) == 3
    for index, reply in enumerate(replies):
        message = requests[index]["messages"][-1]
        assert message["role"] == "user"
        content = message["content"]
        if isinstance(reply, StrImage):
            assert json.loads(content[0]["text"])["value"] == reply.value
            assert [part["image_url"]["url"] for part in content[1:]] == [
                image.url for image in reply.images
            ]
        else:
            assert json.loads(content)["value"] == reply.value
    image_messages = [message for message in messages if message.images]
    assert [message.images for message in image_messages] == [[images[0]], images]
    assert all(image.url not in message.content for message in messages for image in images)


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
    io = bind_user_io([Str(value="answer")])
    token = bind_output(None)
    try:
        with pytest.raises(UserInputUnavailableError):
            interact_with_user("status", False)
        with pytest.raises(UserInputUnavailableError):
            interact_with_user("question", True)
    finally:
        reset_output(token)

    assert interact_with_user("question", True) == Str(value="answer")
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
    io = bind_user_io([Str(value="after failure")])
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
    assert interact_with_user("still available", True) == Str(value="after failure")
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
    io = bind_user_io([Str(value="from user")])
    output, messages = agent.invoke()

    assert isinstance(output, Stop)
    assert output.value == "ok"

    user_payloads: list[dict] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            user_payloads.append(json.loads(render_content(message, include_timestamp=False)))
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
