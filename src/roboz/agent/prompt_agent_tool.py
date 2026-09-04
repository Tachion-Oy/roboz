from dataclasses import dataclass
from typing import Any

from roboz.llm import EndpointLike, call_llm_api, get_completion
from roboz.models import Empty, Invoke, Message
from roboz.runtime.pipe import EventPipe
from roboz.tooling.core import Tool
from roboz.tooling.decorators import factory
from roboz.tooling.dependencies import FactoryCtx, ToolDependency

@dataclass(frozen=True)
class PromptAgentCtx(FactoryCtx):
    endpoint: ToolDependency[Any] | EndpointLike
    active_tools: tuple[Tool, ...]
    pipe: EventPipe


@factory
def prompt_agent(input: Empty, messages: list[Message], ctx: PromptAgentCtx) -> Invoke:
    """To create an agent use this as the default tool."""

    endpoint = ctx.endpoint
    if isinstance(endpoint, ToolDependency):
        endpoint = endpoint.resource

    def start_stream_attempt() -> None:
        ctx.pipe.start_message()

    response = get_completion(
        messages=messages,
        active_tools=list(ctx.active_tools),
        call_llm_api=lambda x: call_llm_api(
            endpoint,
            x,
            on_delta=ctx.pipe.emit_message_delta,
            pipe=ctx.pipe,
            start_replacement_stream=start_stream_attempt,
        ),
        error_pipe=ctx.pipe,
        on_attempt_start=start_stream_attempt,
    )
    return Invoke.model_validate(response)
