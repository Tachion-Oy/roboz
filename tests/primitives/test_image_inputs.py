"""Image values through the existing message lifecycle."""

import base64
from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from roboz.llm import estimate_conversation_tokens, get_truncated_messages_for_context
from roboz.llm.endpoints import role_to_user_mapper
from roboz.models import Image, Message, MessageKind, Role, Str
from roboz.models._schema import messages_scrubber, schema_scrubber
from roboz.models._serialization import get_finalized_message
from roboz.models.truncation import LIGHT_MAX_CHARS, Severity, Truncation
from roboz.runtime.persistence import (
    ConversationRun,
    logged_row_to_message,
    message_to_logged_row,
)


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
    output = Str(value="A chart", images=[image], message_kind=MessageKind.USER_NOTIFICATION)
    message = get_finalized_message(output)
    mapped = role_to_user_mapper([message])[0]

    assert mapped == message
    assert mapped.images == [image]
    assert json.loads(mapped.content) == {"value": "A chart"}
    assert "images" not in schema_scrubber(Str.model_json_schema())["properties"]
    assert messages_scrubber([mapped]) == [{
        "role": "user", "content": message.content, "images": [{"url": image.url}],
    }]
    error = message.model_copy(update={"role": Role.ERROR})
    assert role_to_user_mapper([error])[0] == message


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


def test_images_round_trip_through_persisted_messages_and_old_runs_load():
    message = Message(
        role=Role.USER, content="Inspect",
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
