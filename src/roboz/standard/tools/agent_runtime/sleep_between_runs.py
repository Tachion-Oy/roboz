"""Sleep between deterministic Librarian passes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep

from roboz.exceptions import ExternalCallCancelledError
from roboz.models import All, Message, Stop, Str
from roboz.runtime.persistence import active_marker_paths
from roboz.models import NO_MESSAGE
from roboz import FactoryCtx, factory

SLEEP_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class SleepBetweenRunsCtx(FactoryCtx):
    seconds: float
    is_cancelled: Callable[[], bool] | None = None
    conversation_root: Path | None = None
    agent_names: set[str] = field(default_factory=set)


@factory
def sleep_between_runs(
    input: All, messages: list[Message], ctx: SleepBetweenRunsCtx
) -> Str | Stop:
    """Wait for the next pass, or stop when the watched project is idle."""
    seconds = max(0.0, float(ctx.seconds))
    is_cancelled = ctx.is_cancelled
    _raise_if_cancelled(is_cancelled)

    active_markers: tuple[Path, ...] = ()
    if ctx.conversation_root is not None:
        active_markers = active_marker_paths(ctx.conversation_root, ctx.agent_names)
        if not active_markers:
            return Stop(value="sleep_between_runs: project idle")

    slept = 0.0
    while slept < seconds:
        _raise_if_cancelled(is_cancelled)
        chunk = min(SLEEP_POLL_SECONDS, seconds - slept)
        sleep(chunk)
        slept += chunk
        _raise_if_cancelled(is_cancelled)
        if any(not marker.exists() for marker in active_markers):
            return Str(
                value="sleep_between_runs: active run ended",
                truncation=NO_MESSAGE,
            )
    return Str(value=f"sleep_between_runs: slept={seconds}s", truncation=NO_MESSAGE)


def _raise_if_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise ExternalCallCancelledError("Sleep cancelled")


__all__ = ["SleepBetweenRunsCtx", "sleep_between_runs"]
