"""Resource checks are typed operations independent of factory context types."""

from typing import assert_type

from openai import OpenAI

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.llm import LLMEndpoint, TranscriptionEndpoint


def inspect_availability(resource: ExternalDependency) -> bool:
    assert_type(resource.check(), bool)
    return resource.check()


assert_type(ExecutableDependency("python").check(), bool)
assert_type(
    LLMEndpoint(
        client=OpenAI(api_key="type-test-only"), api_name="test", model_name="chat"
    ).check(),
    bool,
)
assert_type(
    TranscriptionEndpoint(
        client=OpenAI(api_key="type-test-only"), api_name="test", model_name="speech"
    ).check(),
    bool,
)
