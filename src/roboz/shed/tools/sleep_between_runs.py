"""Cancellable wait between deterministic Librarian maintenance cycles."""

from collections.abc import Callable
from time import sleep
from typing import Final

from roboz.shed.identifiers import SLEEP_BETWEEN_RUNS_TOOL_NAME
from roboz.shed.tools.contexts import SleepBetweenRunsContext
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import NO_MESSAGE, All, Message, Str
from roboz.runtime.persistence import active_marker_paths
from roboz.tooling.decorators import factory

SLEEP_POLL_SECONDS: Final[float] = 1.0


def _raise_if_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise ExternalCallCancelledError("Sleep cancelled")


@factory
def sleep_between_runs(
    input: All, messages: list[Message], ctx: SleepBetweenRunsContext
) -> Str:
    """Wait between maintenance cycles, waking promptly when a watched run ends.

    Return immediately when the watched agents are inactive. This tool never
    decides whether maintenance is complete; use ``stop_when_watched_agents_inactive`` for that.
    """
    del input, messages
    _raise_if_cancelled(ctx.is_cancelled)
    active_markers = (
        active_marker_paths(ctx.conversation_root, ctx.agent_names)
        if ctx.conversation_root is not None
        else ()
    )
    if ctx.conversation_root is not None and not active_markers:
        return Str(
            value=f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: watched agents inactive",
            truncation=NO_MESSAGE,
        )

    seconds = max(0.0, float(ctx.seconds))
    slept = 0.0
    while slept < seconds:
        _raise_if_cancelled(ctx.is_cancelled)
        chunk = min(SLEEP_POLL_SECONDS, seconds - slept)
        sleep(chunk)
        slept += chunk
        _raise_if_cancelled(ctx.is_cancelled)
        if any(not marker.exists() for marker in active_markers):
            return Str(
                value=f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: active run ended",
                truncation=NO_MESSAGE,
            )
    return Str(
        value=f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: slept={seconds}s",
        truncation=NO_MESSAGE,
    )


__all__ = ["SLEEP_POLL_SECONDS", "SleepBetweenRunsContext", "sleep_between_runs"]
