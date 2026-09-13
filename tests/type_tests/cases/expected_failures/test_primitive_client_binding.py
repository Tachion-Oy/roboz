"""Endpoints reject clients without the specified OpenAI-compatible operations."""

from roboz.llm import LLMEndpoint

# Expected: reportArgumentType; object lacks the OpenAICompatibleChatClient contract.
LLMEndpoint(client=object(), api_name="test", model_name="chat")
