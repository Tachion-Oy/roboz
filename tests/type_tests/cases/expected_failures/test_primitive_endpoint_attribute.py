"""Endpoint member access is checked against the concrete annotation."""

from roboz.models import Message, Str
from roboz import factory
from roboz.llm import LLMEndpoint


@factory
def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    # Expected: reportAttributeAccessIssue; misspelled model_name is not declared.
    return Str(value=ctx.model_nam)
