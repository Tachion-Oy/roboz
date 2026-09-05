"""Tools for sending messages to and requesting input from users."""

from dataclasses import dataclass

from pydantic import ConfigDict, Field

from roboz.models import All, Message, Role, Str
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime.io import interact_with_user
from roboz.tooling.decorators import factory
from roboz.tooling.dependencies import FactoryCtx

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


@dataclass(frozen=True)
class PromptUserCtx(FactoryCtx):
    """Fallback response returned when a user prompt times out."""

    timeout_reply: str


@factory
def prompt_user(input: PromptUser, messages: list[Message], ctx: PromptUserCtx) -> Str:
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


@dataclass(frozen=True)
class MessageCtx(FactoryCtx):
    """Fixed user-facing message bound to a message tool."""

    message: str


@factory
def message_user(input: Str, messages: list[Message], ctx: MessageCtx) -> Str:
    """Send the configured fixed message to the user without awaiting a reply."""
    _ = interact_with_user(ctx.message, with_reply=False)
    return Str(value=ctx.message)


@factory
def prompt_user_at_start(input: All, messages: list[Message], ctx: MessageCtx) -> Str:
    """Prompt the user only at the start of a conversation.

    Use this as a default tool when the agent needs an initial user response.
    """
    if any([m.role == Role.ASSISTANT for m in messages]):
        return Str(value="", truncation=NO_MESSAGE)
    reply = interact_with_user(ctx.message, with_reply=True)
    if reply is None:
        reply = NO_REPLY
    return Str(value=reply)
