from json import JSONDecodeError
from typing import Any
from unittest.mock import Mock

import pytest

from roboz.exceptions import LLMOutputFormatError
from roboz.llm import LLMEndpointRoute, MockLLMEndpoint, get_completion
from roboz.models import Message, Role, Str
from roboz.models.truncation import NO_MESSAGE


@pytest.mark.parametrize(
    "text",
    [
        "A plain verdict.",
        "  spaces\n\n",
        "",
        '{"value":"JSON-looking text"}',
        "```json\n{}\n```",
        "<think>reasoning</think>answer",
    ],
)
def test_default_completion_preserves_raw_text_without_repairs(text: str) -> None:
    endpoint = MockLLMEndpoint([text, "unused"])
    messages = [Message(role=Role.USER, content="Give your verdict.")]
    original = messages.copy()

    assert get_completion(endpoint=endpoint, messages=messages) == text
    assert endpoint.mock_responses == ["unused"]
    assert messages == original


def test_callback_raw_completion_keeps_truncation_and_attempt_hook() -> None:
    visible = Message(role=Role.USER, content="visible")
    hidden = Message(role=Role.USER, content="hidden", truncation=NO_MESSAGE)
    caller = Mock(return_value=("raw text", {"token_input": 3}))
    started = Mock()

    result = get_completion(
        messages=[visible, hidden],
        call_llm_api=caller,
        LlmOutputModel=None,
        on_attempt_start=started,
    )

    assert result == "raw text"
    caller.assert_called_once_with([visible])
    started.assert_called_once_with()


def test_raw_mode_propagates_callback_format_errors_without_repair() -> None:
    error = JSONDecodeError("transport error", "", 0)
    caller = Mock(side_effect=error)
    with pytest.raises(JSONDecodeError) as raised:
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            call_llm_api=caller,
        )
    assert raised.value is error
    assert caller.call_count == 1


@pytest.mark.parametrize("supply_both", [False, True])
def test_endpoint_and_callback_are_exclusive_before_execution(
    supply_both: bool,
) -> None:
    endpoint = MockLLMEndpoint(["unused"])
    caller = Mock()
    started = Mock()
    arguments: dict[str, Any] = {}
    if supply_both:
        arguments.update(endpoint=endpoint, call_llm_api=caller)

    with pytest.raises(ValueError, match="exactly one"):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            on_attempt_start=started,
            **arguments,
        )
    caller.assert_not_called()
    started.assert_not_called()
    assert endpoint.mock_responses == ["unused"]


def test_endpoint_structured_completion_repairs_invalid_output() -> None:
    endpoint = MockLLMEndpoint(["plain text", {"wrong": "schema"}, {"value": "valid"}])
    attempts = Mock()

    result = get_completion(
        endpoint=endpoint,
        messages=[Message(role=Role.USER, content="Return JSON.")],
        LlmOutputModel=Str,
        tries=3,
        on_attempt_start=attempts,
    )

    assert result == {"value": "valid"}
    assert attempts.call_count == 3
    assert endpoint.mock_responses == []


def test_explicit_empty_tool_list_keeps_structured_action_validation() -> None:
    endpoint = MockLLMEndpoint([{"value": "first"}, {"value": "second"}])
    with pytest.raises(LLMOutputFormatError):
        get_completion(
            endpoint=endpoint,
            messages=[Message(role=Role.USER, content="hi")],
            active_tools=[],
            tries=2,
        )
    assert endpoint.mock_responses == []


def test_route_resolves_again_for_each_completion_repair() -> None:
    first = MockLLMEndpoint(["invalid"])
    second = MockLLMEndpoint([{"value": "repaired"}])
    selections = [first, second]

    def select() -> MockLLMEndpoint:
        return selections.pop(0)

    result = get_completion(
        endpoint=LLMEndpointRoute(select),
        messages=[Message(role=Role.USER, content="Return JSON.")],
        LlmOutputModel=Str,
        tries=2,
    )

    assert result == {"value": "repaired"}
    assert selections == []
    assert first.mock_responses == second.mock_responses == []
