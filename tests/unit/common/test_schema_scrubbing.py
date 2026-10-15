"""Agent schemas and messages expose payloads while retaining runtime metadata."""

from datetime import datetime, timezone
import json

import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import (
    AgentBaseModel,
    Empty,
    GRADED,
    Image,
    Invoke,
    Message,
    MessageKind,
    Role,
    Stop,
    Str,
    TruncationSpec,
)
from roboz.models._schema import messages_scrubber
from roboz.models._serialization import get_finalized_message
from roboz.models.truncation import Truncation
from roboz.runtime.persistence import (
    LoggedMessageRow,
    logged_row_to_message,
    message_to_logged_row,
)
from roboz.tooling._prompts import (
    _get_tool_call_schema,
    example_tool,
    get_tool_instructions,
)
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


def test_overridden_runtime_defaults_stay_hidden_in_tool_schemas():
    class ConfiguredInput(Empty):
        truncation: TruncationSpec = GRADED

    class SpecializedInput(ConfiguredInput):
        message_kind: MessageKind | None = Field(default=MessageKind.USER_NOTIFICATION)

    @tool
    def configured_tool(input: SpecializedInput, messages: list[Message]) -> Str:
        """Return a result using the configured runtime defaults."""
        return Str(value="ok")

    schema = _get_tool_call_schema(configured_tool)

    assert set(schema["properties"]) == {"action", "rationale"}
    assert set(schema["required"]) == {"action", "rationale"}
    assert "$defs" not in schema
    assert SpecializedInput().truncation == GRADED
    assert SpecializedInput().message_kind == MessageKind.USER_NOTIFICATION


def test_overridden_metadata_defaults_stay_out_of_result_content():
    class Notification(Str):
        message_kind: MessageKind | None = MessageKind.USER_NOTIFICATION
        token_input: int | None = 0

    output = Notification(value="Hello")
    message = get_finalized_message(output)

    assert json.loads(message.content) == {"value": "Hello"}
    assert message.message_kind == MessageKind.USER_NOTIFICATION
    assert message.token_input == 0
    assert output.model_dump(mode="json")["message_kind"] == "user_notification"
    assert Message.model_validate_json(message.model_dump_json()) == message


def test_overridden_message_defaults_remain_runtime_metadata():
    class NotificationMessage(Message):
        message_kind: MessageKind | None = MessageKind.USER_NOTIFICATION

    message = NotificationMessage(role=Role.USER, content="Hello")

    assert set(NotificationMessage.model_json_schema()["properties"]) == {
        "role", "content", "images",
    }
    assert message.model_dump(mode="json")["message_kind"] == "user_notification"
    assert messages_scrubber([message]) == [{
        "role": "user", "content": "Hello", "images": [],
    }]


def test_runtime_classification_survives_deferred_annotation_resolution():
    class Notification(Str):
        message_kind: "NotificationKind | None" = MessageKind.USER_NOTIFICATION

    NotificationKind = MessageKind
    Notification.model_rebuild()
    message = get_finalized_message(Notification(value="Hello"))

    assert set(Notification.model_json_schema()["properties"]) == {"value"}
    assert json.loads(message.content) == {"value": "Hello"}
    assert message.message_kind == NotificationKind.USER_NOTIFICATION


def test_mutually_recursive_tool_models_hide_overridden_runtime_fields():
    class Parent(Empty):
        message_kind: MessageKind | None = MessageKind.USER_NOTIFICATION
        child: "Child | None" = None

    class Child(Empty):
        parent: Parent | None = None

    Parent.model_rebuild()

    @tool
    def inspect_family(input: Child, messages: list[Message]) -> Str:
        """Inspect the supplied family tree."""
        return Str(value="ok")

    schema = _get_tool_call_schema(inspect_family)

    assert set(schema["properties"]) == {"parent", "action", "rationale"}
    assert set(schema["$defs"]) == {"Parent", "Child"}
    assert set(schema["$defs"]["Parent"]["properties"]) == {"child"}
    assert schema["$defs"]["Child"]["properties"]["parent"]["anyOf"] == [
        {"$ref": "#/$defs/Parent"}, {"type": "null"},
    ]
    assert "message_kind" not in get_tool_instructions([inspect_family])


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_runtime_schema_filter_preserves_payload_aliases_and_required_fields(mode):
    class Categorized(Empty):
        message_kind: MessageKind = Field(alias="runtime_kind")
        category: MessageKind = Field(alias="message_kind")

    schema = Categorized.model_json_schema(mode=mode)

    assert set(schema["properties"]) == {"message_kind"}
    assert schema["required"] == ["message_kind"]
    assert schema["properties"]["message_kind"] == {"$ref": "#/$defs/MessageKind"}
    assert Categorized.model_json_schema(mode=mode, by_alias=False)["required"] == [
        "category",
    ]
    output = Categorized.model_validate({
        "runtime_kind": "user_notification", "message_kind": "user_notification",
    })
    assert output.model_dump(mode="json")["message_kind"] == "user_notification"
    assert json.loads(get_finalized_message(output).content) == {
        "category": "user_notification",
    }


def test_runtime_schema_filter_preserves_model_validators():
    class Notification(Str):
        message_kind: MessageKind | None = MessageKind.USER_NOTIFICATION

        @model_validator(mode="after")
        def require_text(self):
            if not self.value.strip():
                raise ValueError("Notification requires text")
            return self

    assert set(Notification.model_json_schema()["properties"]) == {"value"}
    assert Notification(value="Hello").message_kind == MessageKind.USER_NOTIFICATION
    with pytest.raises(ValidationError, match="Notification requires text"):
        Notification(value=" ")


def test_generic_runtime_defaults_stay_out_of_schemas_and_payloads():
    class Result[T](Empty):
        message_kind: MessageKind | None = MessageKind.USER_NOTIFICATION
        value: T

    schema = Result[str].model_json_schema()
    message = get_finalized_message(Result[str](value="Hello"))

    assert set(schema["properties"]) == {"value"}
    assert schema["properties"]["value"]["type"] == "string"
    assert json.loads(message.content) == {"value": "Hello"}
    assert message.message_kind == MessageKind.USER_NOTIFICATION


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
        "role": role, "content": message.content, "images": [], "attachments": [], **metadata,
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
