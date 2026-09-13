"""Synchronous endpoints reject the SDK's asynchronous client."""

from openai import AsyncOpenAI

from roboz.llm import TranscriptionEndpoint

# Expected: reportArgumentType; the client must implement synchronous operations.
TranscriptionEndpoint(
    client=AsyncOpenAI(api_key="type-test-only"), api_name="test", model_name="audio"
)
