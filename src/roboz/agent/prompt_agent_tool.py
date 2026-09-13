"""Factory tool that asks a model to choose an agent action."""

from dataclasses import dataclass
from typing import Self

from roboz.dependencies import ExternalDependency
from roboz.llm import EndpointLike, call_llm_api, get_completion
from roboz.models import Empty, Invoke, Message
from roboz.runtime.pipe import EventPipe
from roboz.tooling.context import HasExternalDependencies, Materializable
from roboz.tooling.core import Tool
from roboz.tooling.decorators import factory


@dataclass(frozen=True, kw_only=True)
class PromptAgentContext(HasExternalDependencies, Materializable):
    """Model, available actions, and output pipe for one agent prompt."""

    endpoint: EndpointLike
    active_tools: tuple[Tool, ...]
    pipe: EventPipe

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the prompt's endpoint; the agent owns its action-tool graph."""
        return self.endpoint.external_dependencies()

    def materialize(self) -> Self:
        """Initialize the prompt endpoint before the factory callable runs."""
        if isinstance(self.endpoint, Materializable):
            self.endpoint.materialize()
        return self


@factory
def prompt_agent(
    input: Empty, messages: list[Message], ctx: PromptAgentContext
) -> Invoke:
    """Choose and prepare the next available agent action."""
    endpoint = ctx.endpoint

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
