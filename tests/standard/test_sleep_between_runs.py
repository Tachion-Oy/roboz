"""Tests for adaptive waiting between Librarian maintenance passes."""

from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import Empty, Stop
from roboz.runtime.persistence import (
    ConversationRun,
    RunStatus,
    active_marker_paths,
    clear_conversation_active,
    mark_conversation_active,
    utc_iso_z,
)

from roboz.standard.tools.agent_runtime import SleepBetweenRunsCtx, sleep_between_runs

sleep_module = import_module("roboz.standard.tools.agent_runtime.sleep_between_runs")


def _write_run(
    root: Path, *, status: RunStatus, agent_name: str = "orchestrator"
) -> Path:
    started = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    run = ConversationRun(
        conversation_id="run",
        agent_name=agent_name,
        started_at=utc_iso_z(started),
        ended_at=(
            utc_iso_z(started + timedelta(seconds=1))
            if status is not RunStatus.RUNNING
            else None
        ),
        status=status,
    )
    agent_dir = root / agent_name
    path = agent_dir / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(), encoding="utf-8")
    if status is RunStatus.RUNNING:
        mark_conversation_active(agent_dir=agent_dir, conversation_id=run.conversation_id)
    else:
        clear_conversation_active(agent_dir=agent_dir, conversation_id=run.conversation_id)
    return path


def _ctx(root: Path, *, seconds: float = 120) -> SleepBetweenRunsCtx:
    return SleepBetweenRunsCtx(
        seconds=seconds,
        conversation_root=root,
        agent_names={"orchestrator"},
    )


def test_sleep_stops_immediately_when_project_is_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sleep_module, "sleep", lambda _: pytest.fail("idle project must not sleep")
    )
    tool = sleep_between_runs(_ctx(tmp_path))

    out = tool(input=Empty(), messages=[])

    assert isinstance(out, Stop)
    assert out.value == "sleep_between_runs: project idle"


def test_sleep_waits_full_interval_while_active_run_remains_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(tmp_path, status=RunStatus.RUNNING)
    sleeps: list[float] = []
    monkeypatch.setattr(sleep_module, "sleep", sleeps.append)
    tool = sleep_between_runs(_ctx(tmp_path, seconds=2))

    out = tool(input=Empty(), messages=[])

    assert out.value == "sleep_between_runs: slept=2.0s"
    assert sleeps == [1.0, 1.0]


def test_sleep_returns_early_when_active_run_becomes_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(tmp_path, status=RunStatus.RUNNING)

    def finish_run(_: float) -> None:
        _write_run(tmp_path, status=RunStatus.COMPLETED)

    monkeypatch.setattr(sleep_module, "sleep", finish_run)
    tool = sleep_between_runs(_ctx(tmp_path))

    out = tool(input=Empty(), messages=[])

    assert out.value == "sleep_between_runs: active run ended"


def test_sleep_retries_torn_write_until_terminal_document_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_run(tmp_path, status=RunStatus.RUNNING)
    sleeps = 0

    def rewrite(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            path.write_text("{not-json", encoding="utf-8")
        else:
            _write_run(tmp_path, status=RunStatus.FAILED)

    monkeypatch.setattr(sleep_module, "sleep", rewrite)
    tool = sleep_between_runs(_ctx(tmp_path))

    out = tool(input=Empty(), messages=[])

    assert out.value == "sleep_between_runs: active run ended"
    assert sleeps == 2


def test_sleep_returns_early_when_active_run_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(tmp_path, status=RunStatus.RUNNING)
    marker = active_marker_paths(tmp_path, {"orchestrator"})[0]
    monkeypatch.setattr(sleep_module, "sleep", lambda _: marker.unlink())
    tool = sleep_between_runs(_ctx(tmp_path))

    out = tool(input=Empty(), messages=[])

    assert out.value == "sleep_between_runs: active run ended"


def test_sleep_ignores_running_unwatched_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(
        tmp_path,
        status=RunStatus.RUNNING,
        agent_name="unwatched_agent",
    )
    monkeypatch.setattr(
        sleep_module, "sleep", lambda _: pytest.fail("idle project must not sleep")
    )
    tool = sleep_between_runs(_ctx(tmp_path))

    out = tool(input=Empty(), messages=[])

    assert isinstance(out, Stop)


def test_sleep_cancellation_precedes_idle_decision(tmp_path: Path) -> None:
    tool = sleep_between_runs(
        SleepBetweenRunsCtx(
            seconds=120,
            is_cancelled=lambda: True,
            conversation_root=tmp_path,
            agent_names={"orchestrator"},
        )
    )

    with pytest.raises(ExternalCallCancelledError):
        tool(input=Empty(), messages=[])


def test_sleep_remains_generic_without_project_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(sleep_module, "sleep", sleeps.append)
    tool = sleep_between_runs(SleepBetweenRunsCtx(seconds=2))

    out = tool(input=Empty(), messages=[])

    assert out.value == "sleep_between_runs: slept=2.0s"
    assert sleeps == [1.0, 1.0]
