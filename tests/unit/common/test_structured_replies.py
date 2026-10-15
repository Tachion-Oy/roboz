"""Structured replies through real agent execution and provider serialization."""

import json
import logging

import httpx
from openai import OpenAI
import pytest

from roboz import Agent, tool
from roboz.llm import LLMEndpoint, LLMEndpointRoute, MockLLMEndpoint
from roboz.models import AttachmentMetadata, Empty, Image, Message, Role, Str
from roboz.models._schema import messages_scrubber
from roboz.runtime import (
    UserReply, bind_api_user_io, reset_api_user_io, default_event_sinks,
    interact_with_user,
)
from roboz.runtime.persistence import ConversationRun, logged_row_to_message
from roboz.shed.tools.compactification.summarize import conversation_text
from roboz.tools import prompt_user_at_start, stop


class ReplyHost:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.capabilities = []
        self.legacy_prompts = []

    def request_reply(self, message, timeout=None, *, accepts_images):
        self.capabilities.append(accepts_images())
        return next(self.replies)

    def request_input(self, message, timeout=None):
        self.legacy_prompts.append(message)
        return "yes"

    def notify(self, message):
        pass


def attachment(index):
    return AttachmentMetadata(
        id=str(index), filename=f"chart-{index}.png", media_type="image/png",
        size_bytes=5, working_path=f"projects/demo/.robozium-attachments/run/{index}.png",
    )


@pytest.mark.parametrize("content", ["Inspect these", ""])
def test_initial_and_followup_replies_reach_provider_but_projected_history_has_no_pixels(
    tmp_path, caplog, content,
):
    caplog.set_level(logging.DEBUG, logger="roboz")
    images = [Image.from_bytes(f"image-{i}".encode(), media_type="image/png") for i in range(2)]
    metadata = [attachment(i) for i in range(2)]
    reply = UserReply(content=content, images=images, attachments=metadata)
    host = ReplyHost([reply, reply])
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        response = (
            {"action": "prompt_user", "rationale": "Follow up", "value": "More?"}
            if len(requests) == 1 else
            {"action": "stop", "rationale": "Done", "value": "ok"}
        )
        return httpx.Response(200, json={
            "id": "reply", "object": "chat.completion", "created": 0, "model": "vision",
            "choices": [{"index": 0, "message": {
                "role": "assistant", "content": json.dumps(response),
            }, "finish_reason": "stop"}],
        })

    def project(message):
        # Even a mutating host projection only sees its private copy.
        message.images.clear()
        return message

    with OpenAI(
        api_key="scripted-key", base_url="https://provider.invalid/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        endpoint = LLMEndpoint(
            client=client, api_name="test", model_name="vision", accepts_images=True,
            stream=False,
        )
        sinks = default_event_sinks(
            data_path=tmp_path, include_cli=False, persistence_message_projection=project,
        )
        agent = Agent(
            name="image_replies", tools=[stop], system_prompt="Inspect images.",
            default_tools=[prompt_user_at_start("Start?")], agent_endpoint=endpoint,
            event_sinks=sinks,
        )
        token = bind_api_user_io(host)
        try:
            result, messages = agent.invoke()
        finally:
            reset_api_user_io(token)

    assert result.value == "ok"
    assert host.capabilities == [True, True]
    image_messages = [message for message in messages if message.images]
    assert len(image_messages) == 2
    for message in image_messages:
        assert message.role == Role.USER
        assert message.images == images
        assert message.attachments == metadata
        assert json.loads(message.content)["value"] == content
        assert json.loads(message.content)["attachments"][0]["filename"] == "chart-0.png"
        assert "attachments" not in messages_scrubber([message])[0]
    provider_images = [
        part["image_url"]
        for message in requests[-1]["messages"] if isinstance(message["content"], list)
        for part in message["content"] if part["type"] == "image_url"
    ]
    assert provider_images == [image.model_dump(exclude_none=True) for image in images] * 2
    assert all(set(message) == {"role", "content"} for message in requests[-1]["messages"])
    stored = next(tmp_path.rglob("*.json")).read_text()
    run = ConversationRun.model_validate_json(stored)
    restored = [logged_row_to_message(row) for row in run.messages]
    assert [m.attachments for m in restored if m.attachments] == [metadata, metadata]
    summary_input = conversation_text(restored)
    for item in metadata:
        assert item.filename in summary_input
        assert item.working_path in summary_input
    for image in images:
        encoded = image.url.partition(",")[2]
        assert encoded not in stored + summary_input + caplog.text


def test_receiving_nested_agent_capability_and_permission_text_path():
    host = ReplyHost([UserReply("child reply"), UserReply("parent reply")])
    child = Agent(
        name="child", tools=[stop], system_prompt="Child",
        default_tools=[prompt_user_at_start("Child?")],
        agent_endpoint=MockLLMEndpoint([
            {"action": "stop", "rationale": "Done", "value": "child done"},
        ], accepts_images=False),
    )

    @tool
    def nested(input: Empty, messages: list[Message]) -> Str:
        """Run the specialist and check a text-only permission prompt."""
        child.invoke()
        assert interact_with_user("Allow read?", with_reply=True) == "yes"
        return Str(value="child complete")

    parent = Agent(
        name="parent", tools=[nested, stop], system_prompt="Parent",
        agent_endpoint=MockLLMEndpoint([
            {"action": "nested", "rationale": "Ask child"},
            {"action": "prompt_user", "rationale": "Ask parent", "value": "Parent?"},
            {"action": "stop", "rationale": "Done", "value": "done"},
        ], accepts_images=True),
    )
    token = bind_api_user_io(host)
    try:
        parent.invoke()
    finally:
        reset_api_user_io(token)
    assert host.capabilities == [False, True]
    assert host.legacy_prompts == ["Allow read?"]


def test_prompt_capability_callback_resolves_live_route():
    selected = MockLLMEndpoint([
        {"action": "stop", "rationale": "Done", "value": "ok"},
    ], accepts_images=True)

    class SwitchingHost(ReplyHost):
        def request_reply(self, message, timeout=None, *, accepts_images):
            assert accepts_images() is True
            selected.accepts_images = False
            assert accepts_images() is False
            return UserReply("text")

    agent = Agent(
        name="routed", tools=[stop], system_prompt="Ask user",
        default_tools=[prompt_user_at_start("Question?")],
        agent_endpoint=LLMEndpointRoute(lambda: selected),
    )
    token = bind_api_user_io(SwitchingHost([]))
    try:
        assert agent.invoke()[0].value == "ok"
    finally:
        reset_api_user_io(token)
