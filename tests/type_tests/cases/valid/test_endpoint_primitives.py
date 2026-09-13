"""Endpoint contexts retain their concrete type for binding and member access."""

from typing import assert_type

from openai import OpenAI

from roboz import HasExternalDependencies, Factory, Message, Str, Tool, factory
from roboz.dependencies import ExternalDependency, ExternalDependencyKind
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    ModelSelector,
    TranscriptionEndpoint,
    with_openrouter_policy,
    with_request_options,
)


@factory
def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    assert_type(ctx.model_name, str)
    assert_type(ctx.temperature, float)
    assert_type(ctx.max_context_tokens, int)
    assert_type(ctx.kind, ExternalDependencyKind)
    return Str(value=f"{ctx.model_name}: {input.value}")


@factory()
def describe_transcription(
    input: Str, messages: list[Message], ctx: TranscriptionEndpoint
) -> Str:
    assert_type(ctx.language, str | None)
    return Str(value=f"{ctx.model_name}: {input.value}")


@factory(chained_to=describe_model)
def describe_next_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    return Str(value=f"{ctx.model_name}: {input.value}")


@factory
def describe_script(input: Str, messages: list[Message], ctx: EndpointLike) -> Str:
    return Str(value=ctx.model_name)


endpoint = LLMEndpoint(
    client=OpenAI(api_key="type-test-only"), api_name="test", model_name="chat"
)
transcription = TranscriptionEndpoint(
    client=OpenAI(api_key="type-test-only"), api_name="test", model_name="speech"
)
assert_type(describe_model, Factory[Str, Str, LLMEndpoint])
assert_type(describe_next_model, Factory[Str, Str, LLMEndpoint])
assert_type(describe_transcription, Factory[Str, Str, TranscriptionEndpoint])
assert_type(describe_script, Factory[Str, Str, LLMEndpoint | MockLLMEndpoint])
assert_type(describe_model(endpoint), Tool[Str, Str])
assert_type(describe_model(endpoint).copy(), Tool[Str, Str])
assert_type(
    describe_model(endpoint).external_dependencies(), tuple[ExternalDependency, ...]
)
assert_type(describe_transcription(transcription), Tool[Str, Str])
assert_type(describe_script(MockLLMEndpoint([])), Tool[Str, Str])


def inspect_context(ctx: HasExternalDependencies) -> tuple[ExternalDependency, ...]:
    return ctx.external_dependencies()


assert_type(inspect_context(endpoint), tuple[ExternalDependency, ...])
assert_type(inspect_context(transcription), tuple[ExternalDependency, ...])
assert_type(inspect_context(MockLLMEndpoint([])), tuple[ExternalDependency, ...])
assert_type(
    inspect_context(MockTranscriptionEndpoint([])), tuple[ExternalDependency, ...]
)
assert_type(with_request_options(endpoint, extra_body={}), LLMEndpoint)
assert_type(with_openrouter_policy(endpoint), LLMEndpoint)
assert_type(
    ModelSelector({"chat": endpoint}, default=endpoint).selected_endpoint, LLMEndpoint
)
