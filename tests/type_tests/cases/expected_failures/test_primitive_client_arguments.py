"""The client contract checks request controls instead of accepting arbitrary kwargs."""

from roboz.llm import LLMEndpoint


def check(endpoint: LLMEndpoint) -> object:
    # Expected: reportArgumentType; model-discovery timeout must be a float.
    return endpoint.client.models.list(timeout="ten seconds")
