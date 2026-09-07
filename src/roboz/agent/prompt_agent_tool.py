"""Factory tool that asks a model to choose an agent action."""

from functools import partial

from roboz.llm import call_llm_api, get_completion
from roboz.models import Empty, Invoke, Message
from roboz.tooling.context import Ctx, _prepare_context
from roboz.tooling.decorators import factory


@factory
def prompt_agent(input: Empty, messages: list[Message], ctx: Ctx) -> Invoke:
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


prompt_agent._prepare_ctx = partial(
    _prepare_context, required=("endpoint", "active_tools", "pipe")
)
