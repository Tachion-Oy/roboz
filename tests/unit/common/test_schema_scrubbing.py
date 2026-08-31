"""Unit tests for schema scrubbing and agent-facing surface verification.

Tests derive expectations from NonAgentFacingFields via extract_fields_and_type_names().
They use real Pydantic models. No hardcoded field names—if you change what's excluded,
tests adapt automatically.
"""

import json

import pytest
from pydantic import ValidationError

from roboz.agent.core import Agent
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import (
    AgentBaseModel,
    Empty,
    Message,
    MessageKind,
    Stop,
    Str,
)
from roboz.models._schema import (
    extract_fields_and_type_names,
    messages_scrubber,
    schema_scrubber,
)
from roboz.models._serialization import get_finalized_message
from roboz.models._telemetry import NonAgentFacingFields
from roboz.tooling._prompts import _get_tool_call_schema, example_tool
from roboz.tooling.decorators import tool


@tool
def schema_scrub_test_tool(input: Empty, messages: list[Message]) -> Str:
    """Minimal tool for testing get_formatted_output_and_pipe message content."""
    return Str(value="ok")


# ---------- A. extract_fields_and_type_names ----------


def test_extract_fields_and_type_names_derives_from_exclusion_class():
    """Default extraction exactly matches explicit NonAgentFacingFields extraction."""

    expected_schema = NonAgentFacingFields.model_json_schema()
    expected_fields = frozenset(expected_schema.get("properties", {}).keys())
    expected_fields_explicit, expected_defs_explicit = extract_fields_and_type_names(
        NonAgentFacingFields
    )

    fields, defs = extract_fields_and_type_names()
    assert fields == expected_fields
    assert fields == expected_fields_explicit
    assert defs == expected_defs_explicit


def test_extract_fields_includes_dataclass_field_types_for_truncation():
    """Truncation dataclass yields Severity (etc.) so $defs can be scrubbed with truncation."""
    _, defs = extract_fields_and_type_names()
    assert "Severity" in defs


def test_non_agent_facing_fields_stay_in_sync_with_agent_base_model():
    """Internal scrub list must include all fields inherited by agent models."""
    assert set(NonAgentFacingFields.model_fields) == set(AgentBaseModel.model_fields)


# ---------- B. scrub_schema_for_agent ----------


def test_scrub_schema_removes_excluded_fields_from_model_schema():
    """Scrubbed schema excludes whatever NonAgentFacingFields declares. Uses real model."""
    schema = Empty.model_json_schema()
    excluded_fields, excluded_defs = extract_fields_and_type_names()

    result = schema_scrubber(schema)

    for f in excluded_fields:
        assert f not in result.get("properties", {})
        assert f not in result.get("required", [])
    for d in excluded_defs:
        assert d not in result.get("$defs", {})


def test_scrub_schema_removes_empty_defs():
    """When all $defs are scrubbed, the $defs key is removed entirely."""
    _, excluded_defs = extract_fields_and_type_names()
    if not excluded_defs:
        return  # Nothing to scrub
    schema = Empty.model_json_schema()
    result = schema_scrubber(schema)
    # Empty's schema has only defs for excluded types; after scrub, $defs is removed entirely
    for d in excluded_defs:
        assert d not in result.get("$defs", {})
    # When all defs were excluded, the key is gone
    original_defs = set(schema.get("$defs", {}).keys())
    if original_defs and original_defs <= excluded_defs:
        assert "$defs" not in result


def test_scrub_schema_preserves_non_excluded_fields():
    """Fields not in exclusion spec remain intact. Uses real model (Stop)."""
    schema = Stop.model_json_schema()
    excluded_fields, _ = extract_fields_and_type_names()
    original_props = set(schema.get("properties", {}).keys())
    expected_remaining = original_props - excluded_fields

    result = schema_scrubber(schema)
    result_props = set(result.get("properties", {}).keys())

    assert expected_remaining <= result_props
    for f in excluded_fields:
        assert f not in result_props


# ---------- C. Agent-facing surface: prompts ----------


def test_tool_schema_excludes_declared_fields():
    """Tool call schema does not contain any excluded fields. Uses real tool."""
    schema = _get_tool_call_schema(example_tool)
    excluded_fields, _ = extract_fields_and_type_names()

    for f in excluded_fields:
        assert f not in schema.get("properties", {})


def test_output_schema_excludes_declared_fields():
    """Output schema does not contain any excluded fields. Uses real model (Empty)."""
    schema = schema_scrubber(Empty.model_json_schema())
    excluded_fields, _ = extract_fields_and_type_names()

    for f in excluded_fields:
        assert f not in schema.get("properties", {})


# ---------- D. Agent-facing surface: message content ----------


def test_formatted_output_message_excludes_declared_fields():
    """Finalized message content does not contain any excluded fields. Uses real model (Stop)."""
    output = Stop(value="done")
    message = get_finalized_message(output, schema_scrub_test_tool)
    content = json.loads(message.content)
    excluded_fields, _ = extract_fields_and_type_names()
    for f in excluded_fields:
        assert f not in content


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
    assert scrubbed == [{"role": "user", "content": message.content}]
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


def test_full_system_prompt_excludes_declared_fields_and_defs():
    """Full system prompt generated via Agent init does not expose excluded schema fields/defs."""
    agent = Agent(
        interaction_mode=None,
        name="schema_scrub_agent",
        tools=[example_tool],
        system_prompt="Test.",
        agent_endpoint=MockLLMEndpoint(
            [dict(action="stop", rationale="", value="unused")]
        ),
        initial_messages=None,
    )
    prompt = agent.full_system_prompt
    excluded_fields, excluded_defs = extract_fields_and_type_names()

    assert prompt
    for f in excluded_fields:
        assert f"'{f}':" not in prompt
        assert f'"{f}":' not in prompt
    for d in excluded_defs:
        assert f"'{d}':" not in prompt
        assert f'"{d}":' not in prompt
