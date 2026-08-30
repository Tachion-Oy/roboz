"""Tests for roboz.agent.background_agent."""

import logging
from collections.abc import Callable
from threading import Event
from typing import cast

import pytest

from roboz.agent.background_agent import BackgroundAgentCtx, run_background_agent
from roboz.agent.core import Agent
from roboz.llm import MockLLMEndpoint
from roboz.models import Empty, Message, Stop
from roboz.runtime import EventPipe
from roboz.runtime.persistence import RunStatus


class _TestAgent(Agent):
    def __init__(self, name: str, behavior: Callable[[], None]) -> None:
        super().__init__(
            name=name,
            system_prompt="Test background lifecycle.",
            agent_endpoint=MockLLMEndpoint([]),
        )
        self._behavior = behavior
        self.calls = 0

    def invoke(
        self, *, input: Empty | None = None, dry_run: bool = False
    ) -> tuple[Stop, list[Message]]:
        del input
        self.calls += 1
        status = RunStatus.FAILED
        self._initialize_pipe_for_invoke(dry_run=dry_run)
        try:
            self._behavior()
            status = RunStatus.COMPLETED
            return Stop(), []
        finally:
            self.pipe.finalize_run(status=status)


def test_run_background_agent_starts_once_while_thread_alive() -> None:
    started = Event()
    release = Event()

    def block() -> None:
        started.set()
        release.wait(timeout=2)

    agent = _TestAgent("blocking", block)
    ctx = BackgroundAgentCtx(agent=agent)
    tool = run_background_agent(ctx)

    try:
        first = tool(input=Empty(), messages=[])
        assert started.wait(timeout=2)

        second = tool(input=Empty(), messages=[])

        assert first.status == "started"
        assert second.status == "alive"
        assert second.checks == 2
        assert agent.calls == 1
        assert ctx.state.thread is not None
        assert ctx.state.thread.is_alive()
    finally:
        release.set()
        assert ctx.state.thread is not None
        ctx.state.thread.join(timeout=2)


def test_run_background_agent_respawns_dead_thread() -> None:
    agent = _TestAgent("returning", lambda: None)
    ctx = BackgroundAgentCtx(agent=agent)
    tool = run_background_agent(ctx)

    first = tool(input=Empty(), messages=[])
    first_thread = ctx.state.thread
    assert first_thread is not None
    first_thread.join(timeout=2)

    second = tool(input=Empty(), messages=[])
    second_thread = ctx.state.thread
    assert second_thread is not None
    second_thread.join(timeout=2)

    assert first.status == "started"
    assert second.status == "restarted"
    assert first_thread is not second_thread
    assert agent.calls == 2


def test_run_background_agent_isolates_exceptions_without_logging_their_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        raise RuntimeError("provider-secret-value")

    agent = _TestAgent("failing", fail)
    ctx = BackgroundAgentCtx(agent=agent)
    tool = run_background_agent(ctx)

    with caplog.at_level(logging.ERROR, logger="roboz.agent.background_agent"):
        out = tool(input=Empty(), messages=[])
        thread = ctx.state.thread
        assert thread is not None
        thread.join(timeout=2)

    assert out.status == "started"
    assert agent.calls == 1
    assert not thread.is_alive()
    assert "error_type=RuntimeError" in caplog.text
    assert "provider-secret-value" not in caplog.text


def test_run_background_agent_times_out_without_started_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SilentAgent:
        name = "silent"
        pipe = EventPipe()

        @staticmethod
        def invoke() -> None:
            return None

    monkeypatch.setattr("roboz.agent.background_agent.BACKGROUND_START_TIMEOUT_S", 0.01)
    ctx = BackgroundAgentCtx(agent=cast(Agent, SilentAgent()))

    with pytest.raises(RuntimeError, match="did not persist its lifecycle event"):
        run_background_agent(ctx)(input=Empty(), messages=[])

    assert ctx.state.thread is not None
    ctx.state.thread.join(timeout=1)
    assert not ctx.state.thread.is_alive()
