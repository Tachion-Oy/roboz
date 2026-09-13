"""Client method names remain checked through a factory's concrete context."""

from roboz import Message, Str, factory
from roboz.llm import LLMEndpoint


@factory
def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    # Expected: reportAttributeAccessIssue; completions has create, not creat.
    ctx.client.chat.completions.creat()
    return input
