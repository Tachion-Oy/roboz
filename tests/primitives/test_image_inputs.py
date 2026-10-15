"""Image values through the existing message lifecycle."""

import base64
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import Field, ValidationError

from roboz import Agent, tool
from roboz.llm import estimate_conversation_tokens, get_truncated_messages_for_context
from roboz.llm.endpoints import MockLLMEndpoint, role_to_user_mapper
from roboz.models import AgentMode, Empty, Image, Invoke, Message, MessageKind, Role, Stop, Str, StrImage
from roboz.models._schema import messages_scrubber
from roboz.models._serialization import get_finalized_message
from roboz.models.truncation import LIGHT_MAX_CHARS, Severity, Truncation
from roboz.runtime.persistence import (
    ConversationRun,
    logged_row_to_message,
    message_to_logged_row,
)
from roboz.runtime.sinks import PersistenceSink
from roboz.tools import stop


def test_image_bytes_encode_with_the_declared_mime_type():
    image = Image.from_bytes(b"image bytes", media_type="image/png", detail="high")
    assert image.url == "data:image/png;base64," + base64.b64encode(b"image bytes").decode()
    assert image.detail == "high"
    assert image.url not in repr(image)


@pytest.mark.parametrize("url", [
    "/tmp/image.png", "file:///tmp/image.png", "https:///image.png",
    "data:text/plain;base64,aGVsbG8=", "data:image/png;base64,",
    "data:image/png;base64,not base64!",
])
def test_image_rejects_invalid_sources(url):
    with pytest.raises(ValidationError):
        Image(url=url)


def test_tool_images_survive_finalization_mapping_and_generic_scrubbing():
    image = Image(url="https://example.com/chart.png")
    output = StrImage(value="A chart", images=[image], message_kind=MessageKind.USER_NOTIFICATION)
    message = get_finalized_message(output)
    mapped = role_to_user_mapper([message])[0]

    assert mapped == message
    assert mapped.images == [image]
    assert json.loads(mapped.content) == {"value": "A chart"}
    assert "images" not in Str.model_json_schema()["properties"]
    assert messages_scrubber([mapped]) == [{
        "role": "user", "content": message.content, "images": [{"url": image.url}],
    }]
    error = message.model_copy(update={"role": Role.ERROR})
    assert role_to_user_mapper([error])[0] == message


def test_text_and_image_results_have_explicit_contracts():
    image = Image(url="https://example.com/chart.png")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Str(value="Text", images=[image])
    with pytest.raises(ValidationError, match="Field required"):
        StrImage(value="Missing images")
    assert StrImage(value="", images=[]).images == []
    assert image.url not in repr(StrImage(value="Chart", images=[image]))
    schema = StrImage.model_json_schema()
    assert set(schema["required"]) == {"value", "images"}
    assert schema["properties"]["images"]["items"] == {"$ref": "#/$defs/Image"}


def test_custom_result_can_declare_images_without_a_shared_image_base():
    class ChartResult(Empty):
        caption: str
        images: list[Image]

    image = Image(url="https://example.com/chart.png")
    message = get_finalized_message(ChartResult(caption="Chart", images=[image]))
    assert message.images == [image]
    assert json.loads(message.content) == {"caption": "Chart"}


@pytest.mark.parametrize("image_aware", [False, True])
def test_chained_tools_receive_only_their_declared_image_fields(image_aware):
    reply = StrImage(value="Chart", images=[Image(url="https://example.com/chart.png")])
    received = []

    @tool
    def produce(input: Empty, messages: list[Message]) -> StrImage:
        """Produce a chart."""
        return reply

    @tool(chained_to=produce)
    def text_only(input: Str, messages: list[Message]) -> Stop:
        """Read only the chart caption."""
        received.append(input)
        return Stop(value=input.value)

    @tool(chained_to=produce)
    def with_images(input: StrImage, messages: list[Message]) -> Stop:
        """Read the chart and its caption."""
        received.append(input)
        return Stop(value=input.value)

    agent = Agent(
        name="image_chain", mode=AgentMode.DETERMINISTIC, agent_endpoint=None,
        default_tools=[produce], tools=[with_images if image_aware else text_only],
    )
    result, messages = agent.invoke()
    assert result.value == "Chart"
    assert len(received) == 1
    assert received[0] is not reply
    if image_aware:
        assert received[0].images == reply.images
    else:
        assert type(received[0]) is Str
        assert not hasattr(received[0], "images")
    assert next(message for message in messages if message.images).images == reply.images


@pytest.mark.parametrize("image_arguments", [
    {},
    {"images": []},
    {"images": [{"url": "https://example.com/chart.png"}]},
])
def test_invocation_payload_preserves_supplied_arguments_without_attachments(image_arguments):
    payload = {
        "action": "inspect_image", "rationale": "Inspect the chart",
        "options": {"caption": None, "labels": []}, **image_arguments,
    }
    invocation = Invoke.model_validate({
        **payload, "endpoint": "test", "token_input": 5,
        "message_kind": MessageKind.USER_NOTIFICATION,
    })

    message = get_finalized_message(invocation)

    assert message.role == Role.ASSISTANT
    assert message.images == []
    assert json.loads(message.content) == payload
    assert message.endpoint == "test"
    assert message.token_input == 5
    assert message.message_kind == MessageKind.USER_NOTIFICATION


def test_agent_can_pass_image_arguments_and_continue_after_tool_result(tmp_path: Path):
    image = Image(url="https://example.com/chart.png")
    received = []

    class ImageInput(Empty):
        images: list[Image] = Field(...)

    @tool
    def inspect_image(input: ImageInput, messages: list[Message]) -> StrImage:
        """Inspect the supplied images."""
        received.extend(input.images)
        return StrImage(value="A chart", images=input.images)

    invocation = {
        "action": "inspect_image", "rationale": "Inspect the chart",
        "images": [image.model_dump(mode="json", exclude_none=True)],
    }
    endpoint = MockLLMEndpoint([
        invocation,
        {"action": "stop", "rationale": "Done", "value": "Finished"},
    ], accepts_images=True)
    sink = PersistenceSink.for_path(tmp_path / "conversations")
    agent = Agent(
        name="image_arguments", tools=[inspect_image, stop],
        system_prompt="Inspect the chart and stop.", agent_endpoint=endpoint,
        event_sinks=(sink,),
    )

    result, messages = agent.invoke()

    assert result.value == "Finished"
    assert received == [image]
    assistant = next(message for message in messages if message.role == Role.ASSISTANT)
    assert json.loads(assistant.content) == invocation
    assert assistant.images == []
    tool_result = next(message for message in messages if message.images)
    assert tool_result.role == Role.USER
    assert tool_result.images == [image]
    assert "images" not in json.loads(tool_result.content)

    assert sink.conversations_location is not None
    persisted = ConversationRun.model_validate_json(sink.conversations_location.read_text())
    assistant_row = next(row for row in persisted.messages if row.role == Role.ASSISTANT)
    assert assistant_row.content == assistant.content
    assert assistant_row.images == []
    assert json.loads(assistant_row.content) == invocation
    result_row = next(row for row in persisted.messages if row.images)
    assert logged_row_to_message(result_row) == tool_result


@pytest.mark.parametrize("severity", list(Severity))
def test_truncation_keeps_or_removes_whole_images(severity):
    image = Image.from_bytes(b"x" * LIGHT_MAX_CHARS, media_type="image/png")
    message = Message(
        role=Role.USER, content="x" * (LIGHT_MAX_CHARS + 10), images=[image],
        truncation=Truncation(threshold=0, severity=severity),
    )
    truncated = get_truncated_messages_for_context([message])
    if severity == Severity.REMOVE:
        assert truncated == []
    elif severity == Severity.STUB:
        assert truncated[0].images == []
    else:
        assert truncated[0].images == [image]
        assert len(truncated[0].content) < len(message.content)
    assert message.images == [image]
    assert estimate_conversation_tokens([message]) == estimate_conversation_tokens(
        [message.model_copy(update={"images": []})]
    )


@pytest.mark.parametrize("role", [Role.USER, Role.ASSISTANT])
def test_images_round_trip_through_persisted_messages_and_old_runs_load(role):
    message = Message(
        role=role, content="Inspect",
        images=[Image.from_bytes(b"bytes", media_type="image/webp")],
    )
    now = datetime.now(timezone.utc)
    row = message_to_logged_row(message, message_id="message", sequence=1, created_at=now)
    run = ConversationRun(
        conversation_id="run", agent_name="test", started_at=now.isoformat(), messages=[row]
    )
    restored = ConversationRun.model_validate_json(run.model_dump_json())
    assert restored.schema_version == 4
    assert logged_row_to_message(restored.messages[0]) == message

    legacy = run.model_dump(mode="json")
    legacy["schema_version"] = 3
    del legacy["messages"][0]["images"]
    restored_legacy = ConversationRun.model_validate(legacy)
    assert logged_row_to_message(restored_legacy.messages[0]).images == []
