from roboz import Empty, Message, factory
from roboz.llm import LLMEndpoint, LLMEndpointRoute


@factory
def fixed(input: Empty, messages: list[Message], ctx: LLMEndpoint) -> Empty:
    return input


def bind(endpoint: LLMEndpoint) -> None:
    # A factory accepting a concrete endpoint does not implicitly accept routes.
    fixed(LLMEndpointRoute(lambda: endpoint))  # reportArgumentType
