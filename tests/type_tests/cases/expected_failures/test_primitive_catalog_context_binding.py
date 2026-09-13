"""A transcription endpoint cannot bind a factory declared with a chat context."""

from roboz import Message, Str, factory
from roboz.llm import LLMEndpoint
from roboz_endpoints import groq


@factory
def describe(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    return Str(value=ctx.model_name)


# Expected: reportArgumentType; TranscriptionEndpoint is not LLMEndpoint.
describe(groq.whisper_large_v3_turbo)
