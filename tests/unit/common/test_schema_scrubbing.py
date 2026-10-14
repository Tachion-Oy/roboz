"""Agent schemas and messages expose payloads while retaining runtime metadata."""

from datetime import datetime, timezone
import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import (
    AgentBaseModel,
    Empty,
    Image,
    Invoke,
    Message,
    MessageKind,
    Role,
    Stop,
    Str,
)
from roboz.models._schema import messages_scrubber
from roboz.models._serialization import get_finalized_message
from roboz.models.truncation import Truncation
from roboz.runtime.persistence import (
    LoggedMessageRow,
    logged_row_to_message,
    message_to_logged_row,
)
from roboz.tooling._prompts import _get_tool_call_schema, example_tool
from roboz.tooling.decorators import tool


@tool
def schema_scrub_test_tool(input: Empty, messages: list[Message]) -> Str:
    """Minimal tool for testing get_formatted_output_and_pipe message content."""
    return Str(value="ok")


@pytest.mark.parametrize("model, properties", [
    (AgentBaseModel, set()),
    (Empty, set()),
    (Stop, {"value"}),
    (Message, {"role", "content", "images"}),
])
def test_direct_model_schema_excludes_runtime_metadata(model, properties):
    schema = model.model_json_schema()

    assert set(schema["properties"]) == properties
    assert set(schema.get("required", [])) <= properties
    assert {"Truncation", "Severity", "MessageKind"}.isdisjoint(schema.get("$defs", {}))


def test_tool_schema_retains_types_shared_by_payload_and_runtime_metadata():
    class PolicyInput(Empty):
        policy: Truncation
        category: MessageKind

    @tool
    def inspect_policy(input: PolicyInput, messages: list[Message]) -> Str:
        """Inspect the supplied policy and category."""
        return Str(value="ok")

    schema = _get_tool_call_schema(inspect_policy)

    assert set(schema["properties"]) == {"policy", "category", "action", "rationale"}
    assert schema["properties"]["policy"] == {"$ref": "#/$defs/Truncation"}
    assert schema["properties"]["category"] == {"$ref": "#/$defs/MessageKind"}
    assert schema["$defs"]["Truncation"]["properties"]["severity"] == {
        "$ref": "#/$defs/Severity"
    }
    assert schema["$defs"]["MessageKind"]["enum"] == list(MessageKind)
    assert schema["$defs"]["Severity"]["enum"] == [1, 2, 3]


@pytest.mark.parametrize("model", [AgentBaseModel, Empty, Str])
def test_inherited_attachments_are_not_advertised_in_model_schemas(model):
    assert "images" in model.model_fields
    schema = model.model_json_schema()
    assert "images" not in schema.get("properties", {})
    assert "Image" not in schema.get("$defs", {})


def test_schema_preserves_explicit_image_field_definition():
    class PhotoInput(Empty):
        photo: Image

    schema = PhotoInput.model_json_schema()

    assert "images" not in schema["properties"]
    assert schema["properties"]["photo"] == {"$ref": "#/$defs/Image"}
    assert schema["$defs"]["Image"] == Image.model_json_schema()


def test_schema_preserves_image_definition_in_recursive_payload():
    class PhotoAlbum(BaseModel):
        photo: Image
        related: "PhotoAlbum | None" = None

    class PhotoInput(Empty):
        albums: list[PhotoAlbum]

    schema = PhotoInput.model_json_schema()

    assert schema["properties"]["albums"]["items"] == {"$ref": "#/$defs/PhotoAlbum"}
    album = schema["$defs"]["PhotoAlbum"]["properties"]
    assert album["photo"] == {"$ref": "#/$defs/Image"}
    assert {"$ref": "#/$defs/PhotoAlbum"} in album["related"]["anyOf"]
    assert schema["$defs"]["Image"] == Image.model_json_schema()


def test_tool_input_can_explicitly_declare_and_inherit_image_arguments():
    class ImagesInput(Empty):
        images: list[Image] = Field(...)

    class SpecializedImagesInput(ImagesInput):
        pass

    @tool
    def inspect_images(input: SpecializedImagesInput, messages: list[Message]) -> Str:
        """Inspect the supplied images."""
        return Str(value="Inspected", images=input.images)

    schema = _get_tool_call_schema(inspect_images)

    assert schema["properties"]["images"]["items"] == {"$ref": "#/$defs/Image"}
    assert "images" in schema["required"]
    assert schema["$defs"]["Image"] == Image.model_json_schema()


def test_tool_schema_exposes_only_invocation_arguments():
    schema = _get_tool_call_schema(example_tool)

    assert set(schema["properties"]) == {"action", "rationale", "value"}
    assert "$defs" not in schema


@pytest.mark.parametrize("model, payload, role", [
    (Invoke, {"action": "example_tool", "rationale": "Check", "value": 0}, Role.ASSISTANT),
    (Str, {"value": "ok"}, Role.USER),
    (Stop, {"value": "done"}, Role.USER),
])
def test_populated_runtime_metadata_survives_storage_but_stays_out_of_agent_payloads(
    model, payload, role,
):
    metadata = {
        "truncation": [{"threshold": 0, "severity": 1}, {"threshold": 5, "severity": 2}],
        "message_kind": "user_notification",
        "endpoint": "test-endpoint",
        "model": "test-model",
        "token_input": 0,
        "token_output": 42,
    }
    output = model.model_validate(payload | metadata)
    message = get_finalized_message(output, schema_scrub_test_tool)
    expected_payload = payload if role == Role.ASSISTANT else {
        "caller": schema_scrub_test_tool.name, **payload,
    }

    assert json.loads(message.content) == expected_payload
    assert message.model_dump(mode="json") == {
        "role": role, "content": message.content, "images": [], **metadata,
    }
    assert messages_scrubber([message]) == [{
        "role": role, "content": message.content, "images": [],
    }]
    assert Message.model_validate_json(message.model_dump_json()) == message

    row = message_to_logged_row(
        message, message_id="message", sequence=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    restored = LoggedMessageRow.model_validate_json(row.model_dump_json())
    assert logged_row_to_message(restored) == message


def test_finalized_message_preserves_unicode_and_json_escaping() -> None:
    value = '🟡 "quoted" \\ path\nnext line'

    message = get_finalized_message(Str(value=value))

    assert "🟡" in message.content
    assert "\\ud83d" not in message.content.lower()
    assert '\\"quoted\\"' in message.content
    assert "\\\\ path" in message.content
    assert "\\nnext line" in message.content
    assert json.loads(message.content) == {"value": value}


@pytest.mark.parametrize(
    "value",
    [
        r"\n",
        r"\\n",
        r'C:\new\folder\"quoted"',
        "literal backslash before newline: \\n",
    ],
)
def test_finalized_message_preserves_literal_escape_sequences(value: str) -> None:
    message = get_finalized_message(Str(value=value))

    assert json.loads(message.content) == {"value": value}


def test_message_kind_is_not_agent_facing_in_scrubbed_messages():
    """`message_kind` is internal metadata and must be stripped before LLM calls."""
    message = get_finalized_message(
        Str(value="bootstrap"), message_kind=MessageKind.STARTUP_CONTEXT
    )
    assert message.message_kind == MessageKind.STARTUP_CONTEXT

    scrubbed = messages_scrubber([message])
    assert scrubbed == [{"role": "user", "content": message.content, "images": []}]
    assert "message_kind" not in scrubbed[0]


def test_finalized_message_inherits_output_message_kind() -> None:
    message = get_finalized_message(
        Str(value="routine", message_kind=MessageKind.LIBRARIAN_ROUTINE)
    )
    overridden = get_finalized_message(
        Str(value="routine", message_kind=MessageKind.LIBRARIAN_ROUTINE),
        message_kind=MessageKind.LIBRARIAN_ACTIVITY,
    )

    assert message.message_kind == MessageKind.LIBRARIAN_ROUTINE
    assert overridden.message_kind == MessageKind.LIBRARIAN_ACTIVITY


def test_message_kind_rejects_unknown_strings() -> None:
    with pytest.raises(ValidationError):
        Str.model_validate({"value": "routine", "message_kind": "unknown_kind"})


def test_full_system_prompt_excludes_runtime_fields_and_defs():
    """Full system prompt generated via Agent init does not expose excluded schema fields/defs."""
    agent = Agent(
        name="schema_scrub_agent",
        tools=[example_tool],
        system_prompt="Test.",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="unused")]
        ),
        initial_messages=None,
    )
    prompt = agent.full_system_prompt

    assert prompt
    assert "images" not in prompt
    for f in ("truncation", "message_kind", "endpoint", "model", "token_input", "token_output"):
        assert f"'{f}':" not in prompt
        assert f'"{f}":' not in prompt
    for d in ("Truncation", "Severity", "MessageKind"):
        assert f"'{d}':" not in prompt
        assert f'"{d}":' not in prompt
