"""Run agents in background threads."""

import logging
import threading
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Literal

from roboz.agent.core import Agent
from roboz.models import Empty, Message
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime.events import PipeEvent, RunLifecycleEvent
from roboz.tooling.decorators import factory
from roboz.tooling.dependencies import FactoryCtx

logger = logging.getLogger(__name__)
BACKGROUND_START_TIMEOUT_S = 5.0

type BackgroundAgentPhase = Literal["started", "alive", "restarted"]


@dataclass
class BackgroundAgentState:
    thread: threading.Thread | None = None
    checks: int = 0
    started_monotonic: float | None = None


@dataclass(frozen=True)
class BackgroundAgentCtx(FactoryCtx):
    agent: Agent
    state: BackgroundAgentState = field(default_factory=BackgroundAgentState)


class BackgroundAgentStatus(Empty):
    """Evolving heartbeat for a background daemon's trigger tool."""

    status: BackgroundAgentPhase
    agent: str
    checks: int
    uptime: str


def _fmt_uptime(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _invoke_agent(ctx: BackgroundAgentCtx) -> None:
    agent = ctx.agent
    logger.info(
        "Background agent invoke starting (name=%s, thread=%s)",
        agent.name,
        threading.current_thread().name,
    )
    try:
        agent.invoke()
    except Exception as error:  # noqa: BLE001 - background agents must not crash callers
        logger.error(
            "Background agent failed (name=%s, error_type=%s)",
            agent.name,
            type(error).__name__,
        )
    finally:
        logger.info("Background agent invoke returned (name=%s)", agent.name)


def _status(
    ctx: BackgroundAgentCtx,
    *,
    status: BackgroundAgentPhase,
    checks: int,
    started_monotonic: float,
) -> BackgroundAgentStatus:
    return BackgroundAgentStatus(
        status=status,
        agent=ctx.agent.name,
        checks=checks,
        uptime=_fmt_uptime(time.monotonic() - started_monotonic),
        truncation=NO_MESSAGE,
    )


@factory
def run_background_agent(
    input: Empty, messages: list[Message], ctx: BackgroundAgentCtx
) -> BackgroundAgentStatus:
    """Start a background agent in a daemon thread and return a heartbeat."""
    ctx.state.checks += 1
    checks = ctx.state.checks
    thread = ctx.state.thread
    if thread is not None and thread.is_alive():
        started_monotonic = ctx.state.started_monotonic or time.monotonic()
        return _status(
            ctx, status="alive", checks=checks, started_monotonic=started_monotonic
        )

    # First start, or the previous daemon thread has died and we respawn it.
    status: BackgroundAgentPhase = "restarted" if thread is not None else "started"
    started_monotonic = time.monotonic()
    ctx.state.started_monotonic = started_monotonic
    thread = threading.Thread(
        target=_invoke_agent,
        args=(ctx,),
        daemon=True,
        name=f"background-agent-{ctx.agent.name}",
    )
    ctx.state.thread = thread
    logger.info(
        "Spawning background agent thread (name=%s, status=%s, thread=%s)",
        ctx.agent.name,
        status,
        thread.name,
    )
    started = Event()

    def signal_started(event: PipeEvent) -> None:
        if isinstance(event, RunLifecycleEvent) and event.kind == "started":
            started.set()

    unsubscribe = ctx.agent.pipe.add_sink(signal_started)
    try:
        thread.start()
        if not started.wait(BACKGROUND_START_TIMEOUT_S):
            state = "alive" if thread.is_alive() else "stopped"
            raise RuntimeError(
                "background agent did not persist its lifecycle event "
                f"within {BACKGROUND_START_TIMEOUT_S:g}s (thread={state})"
            )
    finally:
        unsubscribe()
    return _status(
        ctx, status=status, checks=checks, started_monotonic=started_monotonic
    )


__all__ = [
    "BackgroundAgentCtx",
    "BackgroundAgentPhase",
    "BackgroundAgentStatus",
    "run_background_agent",
]
