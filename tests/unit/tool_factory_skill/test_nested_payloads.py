"""A tool handoff preserves nested types without sharing mutable state."""

import pytest
from pydantic import Field, SerializeAsAny, ValidationError

from roboz import Empty, Invoke, Message, Str, tool


class Payload(Empty):
    values: list[int]


class Envelope(Empty):
    payload: SerializeAsAny[Empty]
    hidden: str = Field(default="default", exclude=True)


@tool
def inspect_payload(input: Envelope, messages: list[Message]) -> Str:
    assert isinstance(input.payload, Payload)
    input.payload.values.append(2)
    return Str(value=input.hidden)


def test_handoff_preserves_concrete_types_and_detaches_nested_mutation():
    original = Envelope(payload=Payload(values=[1]), hidden="excluded")
    result = inspect_payload(input=original, messages=[])
    assert result.value == "default"
    assert isinstance(original.payload, Payload)
    assert original.payload.values == [1]


def test_wire_invocation_is_still_projected_and_validated():
    @tool
    def echo(input: Str, messages: list[Message]) -> Str:
        return input

    assert (
        echo(
            input=Invoke(action="echo", rationale="test", value="ok"), messages=[]
        ).value
        == "ok"
    )
    with pytest.raises(ValidationError):
        echo(input=Invoke(action="echo", rationale="test", value=[]), messages=[])
