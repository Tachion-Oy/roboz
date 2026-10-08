"""Resource checks are typed operations independent of factory context types."""

from typing import assert_type

from openai import OpenAI

from roboz.dependencies import DependencyFailure, ExecutableDependency, ExternalDependency
from roboz.llm import LLMEndpoint, TranscriptionEndpoint


def inspect_availability(resource: ExternalDependency) -> DependencyFailure | None:
    assert_type(resource.check(), DependencyFailure | None)
    return resource.check()


assert_type(ExecutableDependency("python").check(), DependencyFailure | None)
assert_type(
    LLMEndpoint(
        client=OpenAI(api_key="type-test-only"), api_name="test", model_name="chat"
    ).check(),
    DependencyFailure | None,
)
assert_type(
    TranscriptionEndpoint(
        client=OpenAI(api_key="type-test-only"), api_name="test", model_name="speech"
    ).check(),
    DependencyFailure | None,
)
