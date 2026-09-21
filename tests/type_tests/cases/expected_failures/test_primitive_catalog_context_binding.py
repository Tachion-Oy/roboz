"""A transcription endpoint cannot bind a factory declared with a chat context."""

from roboz.models import Message, Str
from roboz import factory
from roboz.llm import LLMEndpoint
from roboz.endpoints.inventory import groq


@factory
def describe(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    return Str(value=ctx.model_name)


# Expected: reportArgumentType; TranscriptionEndpoint is not LLMEndpoint.
describe(groq.whisper_large_v3_turbo)
