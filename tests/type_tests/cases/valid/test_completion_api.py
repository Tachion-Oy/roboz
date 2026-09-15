from collections.abc import Sequence
from typing import Any, assert_type

from pydantic import BaseModel

from roboz.llm import LLMTelemetryDict, MockLLMEndpoint, get_completion
from roboz.llm.completion import JSONDict
from roboz.models import Message, Role, Str
from roboz.tooling import Tool

messages = [Message(role=Role.USER, content="hi")]
endpoint = MockLLMEndpoint(["raw", {"value": "structured"}])


def caller(messages: list[Message]) -> tuple[str, LLMTelemetryDict]:
    return "text", {}


assert_type(get_completion(endpoint=endpoint, messages=messages), str)
assert_type(get_completion(call_llm_api=caller, messages=messages), str)
assert_type(
    get_completion(endpoint=endpoint, messages=messages, LlmOutputModel=None), str
)
assert_type(
    get_completion(endpoint=endpoint, messages=messages, LlmOutputModel=Str), JSONDict
)
assert_type(
    get_completion(call_llm_api=caller, messages=messages, LlmOutputModel=Str), JSONDict
)
assert_type(
    get_completion(endpoint=endpoint, messages=messages, active_tools=[]), JSONDict
)
assert_type(
    get_completion(call_llm_api=caller, messages=messages, active_tools=[]), JSONDict
)


def dynamic_options(
    model: type[BaseModel] | None, tools: Sequence[Tool] | None
) -> None:
    assert_type(
        get_completion(endpoint=endpoint, messages=messages, LlmOutputModel=model),
        str | JSONDict,
    )
    assert_type(
        get_completion(call_llm_api=caller, messages=messages, active_tools=tools),
        str | JSONDict,
    )
    assert_type(
        get_completion(
            endpoint=endpoint,
            messages=messages,
            LlmOutputModel=model,
            active_tools=tools,
        ),
        str | JSONDict,
    )


# Previously annotated lists remain accepted when text responses are added.
def previous_mock_script(
    responses: list[dict[str, Any] | Exception],
) -> MockLLMEndpoint:
    return MockLLMEndpoint(responses)


def text_mock_script(responses: list[str]) -> MockLLMEndpoint:
    return MockLLMEndpoint(responses)
