"""Tools for sending messages to and requesting input from users."""

from functools import partial

from pydantic import ConfigDict, Field

from roboz.models import All, Message, Role, Str
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime.io import interact_with_user
from roboz.tooling.context import Ctx, _prepare_context
from roboz.tooling.decorators import factory

NO_REPLY = "The user did not respond"


class PromptUser(Str):
    """User prompt text with an optional bounded reply wait."""

    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "If set, wait at most this many seconds for a reply before continuing. "
            "The user can reply at any time before then to interrupt the wait."
        ),
    )
    model_config = ConfigDict(extra="forbid")


@factory
def prompt_user(input: PromptUser, messages: list[Message], ctx: Ctx) -> Str:
    """Send the user a message and wait for their reply.

    Set a timeout only when the task requires a bounded wait; otherwise wait
    indefinitely. A timed-out request continues with the configured fallback.
    """
    reply = interact_with_user(
        input.value, with_reply=True, timeout=input.timeout_seconds
    )
    if reply is None:
        reply = ctx.timeout_reply
    return Str(value=reply)


@factory
def message_user(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    """Send the configured fixed message to the user without awaiting a reply."""
    _ = interact_with_user(ctx.message, with_reply=False)
    return Str(value=ctx.message)


@factory
def prompt_user_at_start(input: All, messages: list[Message], ctx: Ctx) -> Str:
    """Prompt the user only at the start of a conversation.

    Use this as a default tool when the agent needs an initial user response.
    """
    if any([m.role == Role.ASSISTANT for m in messages]):
        return Str(value="", truncation=NO_MESSAGE)
    reply = interact_with_user(ctx.message, with_reply=True)
    if reply is None:
        reply = NO_REPLY
    return Str(value=reply)


prompt_user._prepare_ctx = partial(_prepare_context, required=("timeout_reply",))
message_user._prepare_ctx = partial(_prepare_context, required=("message",))
prompt_user_at_start._prepare_ctx = partial(_prepare_context, required=("message",))
