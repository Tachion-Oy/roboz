from dataclasses import dataclass

from pydantic import ConfigDict, Field

from roboz.models import All, Message, Role, Str
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime.io import interact_with_user
from roboz.tooling.decorators import factory
from roboz.tooling.dependencies import FactoryCtx

NO_REPLY = "The user did not respond"


class PromptUser(Str):
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
    timeout_reply: str


@factory
def prompt_user(input: PromptUser, messages: list[Message], ctx: PromptUserCtx) -> Str:
    """Send the user a message (usually a question) by using the 'value' field.
    Optionally set 'timeout_seconds' to wait only that long for a reply; if the user
    does not respond in time the process continues with a default reply. Use timeout only when it is required by your process, otherwise waiting indefinitely is fine."""
    reply = interact_with_user(
        input.value, with_reply=True, timeout=input.timeout_seconds
    )
    if reply is None:
        reply = ctx.timeout_reply
    return Str(value=reply)


@dataclass(frozen=True)
class MessageCtx(FactoryCtx):
    message: str


@factory
def message_user(input: Str, messages: list[Message], ctx: MessageCtx) -> Str:
    """Sends a fixed message to the user defined via the context."""
    _ = interact_with_user(ctx.message, with_reply=False)
    return Str(value=ctx.message)


@factory
def prompt_user_at_start(input: All, messages: list[Message], ctx: MessageCtx) -> Str:
    """Prompts the user at the start of the conversation only.
    Intended to be used as a default message."""
    if any([m.role == Role.ASSISTANT for m in messages]):
        return Str(value="", truncation=NO_MESSAGE)
    reply = interact_with_user(ctx.message, with_reply=True)
    if reply is None:
        reply = NO_REPLY
    return Str(value=reply)
