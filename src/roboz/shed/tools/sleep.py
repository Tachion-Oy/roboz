"""Interruptible wait for background work, such as Librarian cycles or subagents."""

from time import sleep as pause
from typing import Final

from pydantic import Field

from roboz.shed.identifiers import SLEEP_TOOL_NAME
from roboz.shed.tools.contexts import SleepContext
from roboz.models import NO_MESSAGE, Empty, Message, Str
from roboz.runtime.persistence import active_marker_paths
from roboz.tooling.decorators import factory

SLEEP_POLL_SECONDS: Final[float] = 1.0
SLEEP_PROGRESS_SECONDS: Final[float] = 3.0


class SleepFor(Empty):
    """How long to wait."""

    seconds: float | None = Field(
        default=None, gt=0, description="Seconds to wait; omit for the default."
    )


def _raise_if_stopped(ctx: SleepContext) -> None:
    for signal in ctx.pipe.control_signals:
        signal.raise_if_set()


@factory
def sleep(input: SleepFor, messages: list[Message], ctx: SleepContext) -> Str:
    """Wait, then check on background work again.

    Use it when the user is waiting for background work and you have nothing
    else to do. After each wait, read the work's progress and wait again until
    it is done. The user can interrupt the wait.
    """
    del messages
    _raise_if_stopped(ctx)
    active_markers = (
        active_marker_paths(ctx.conversation_root, ctx.agent_names)
        if ctx.conversation_root is not None
        else ()
    )
    if ctx.conversation_root is not None and not active_markers:
        return Str(
            value=f"{SLEEP_TOOL_NAME}: watched agents inactive", truncation=NO_MESSAGE
        )

    seconds = float(ctx.seconds if input.seconds is None else input.seconds)
    slept = 0.0
    next_progress = SLEEP_PROGRESS_SECONDS
    while slept < seconds:
        chunk = min(SLEEP_POLL_SECONDS, seconds - slept)
        pause(chunk)
        slept += chunk
        _raise_if_stopped(ctx)
        if any(not marker.exists() for marker in active_markers):
            return Str(
                value=f"{SLEEP_TOOL_NAME}: active run ended", truncation=NO_MESSAGE
            )
        if next_progress <= slept < seconds:
            next_progress += SLEEP_PROGRESS_SECONDS
            ctx.pipe.emit_script_output(
                f"{SLEEP_TOOL_NAME}: waited {slept:g}s, {seconds - slept:g}s left\n"
            )
    return Str(value=f"{SLEEP_TOOL_NAME}: slept={seconds}s", truncation=NO_MESSAGE)


__all__ = ["SleepContext", "SleepFor", "sleep"]
