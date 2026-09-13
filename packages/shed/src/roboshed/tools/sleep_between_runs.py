"""Cancellable wait between deterministic Librarian maintenance cycles."""

from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Final

from roboshed.identifiers import SLEEP_BETWEEN_RUNS_TOOL_NAME
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import NO_MESSAGE, All, Message, Stop, Str
from roboz.runtime.persistence import active_marker_paths
from roboshed.tools.contexts import SleepBetweenRunsContext
from roboz.tooling.decorators import factory

SLEEP_POLL_SECONDS: Final[float] = 1.0

_PROJECT_IDLE_STATUS: Final[str] = "project idle"
_ACTIVE_RUN_ENDED_STATUS: Final[str] = "active run ended"
_MIN_SLEEP_SECONDS: Final[float] = 0.0


def _raise_if_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise ExternalCallCancelledError("Sleep cancelled")


@factory
def sleep_between_runs(
    input: All, messages: list[Message], ctx: SleepBetweenRunsContext
) -> Str | Stop:
    """Wait for the next cycle, or stop once the watched project becomes idle."""
    del input, messages
    seconds = max(_MIN_SLEEP_SECONDS, float(ctx.seconds))
    _raise_if_cancelled(ctx.is_cancelled)

    active_markers: tuple[Path, ...] = ()
    if ctx.conversation_root is not None:
        active_markers = active_marker_paths(ctx.conversation_root, ctx.agent_names)
        if not active_markers:
            return Stop(value=f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: {_PROJECT_IDLE_STATUS}")

    slept = _MIN_SLEEP_SECONDS
    while slept < seconds:
        _raise_if_cancelled(ctx.is_cancelled)
        chunk = min(SLEEP_POLL_SECONDS, seconds - slept)
        sleep(chunk)
        slept += chunk
        _raise_if_cancelled(ctx.is_cancelled)
        if any(not marker.exists() for marker in active_markers):
            return Str(
                value=(f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: {_ACTIVE_RUN_ENDED_STATUS}"),
                truncation=NO_MESSAGE,
            )
    return Str(
        value=f"{SLEEP_BETWEEN_RUNS_TOOL_NAME}: slept={seconds}s",
        truncation=NO_MESSAGE,
    )


__all__ = ["SLEEP_POLL_SECONDS", "SleepBetweenRunsContext", "sleep_between_runs"]
