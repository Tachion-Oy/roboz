"""Chat factories reject transcription endpoints despite their common resource base."""

from openai import OpenAI

from roboz import Message, Str, factory
from roboz.llm import LLMEndpoint, TranscriptionEndpoint


@factory
def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    return Str(value=ctx.model_name)


# Expected: reportArgumentType; this factory requires LLMEndpoint.
describe_model(
    TranscriptionEndpoint(
        client=OpenAI(api_key="type-test-only"), api_name="test", model_name="speech"
    )
)
