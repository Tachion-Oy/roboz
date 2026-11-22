"""The sleep tool waits as long as asked, prints nothing, and yields to interrupts."""

import importlib

import pytest

from roboz.exceptions import ExternalCallInterruptedError
from roboz.runtime import EventPipe, PipeEvent, ScriptOutputEvent
from roboz.shed.tools import SleepContext, sleep
from roboz.shed.tools.sleep import SleepFor

sleep_module = importlib.import_module("roboz.shed.tools.sleep")


def test_model_chosen_seconds_override_default_without_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[PipeEvent] = []
    pipe = EventPipe(event_sinks=[events.append])
    waited: list[float] = []
    monkeypatch.setattr(sleep_module, "pause", waited.append)

    result = sleep(SleepContext(pipe=pipe, seconds=120))(SleepFor(seconds=7), [])

    assert result.value == "sleep: slept=7.0s"
    assert sum(waited) == 7
    assert not [event for event in events if isinstance(event, ScriptOutputEvent)]


def test_user_interrupt_ends_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = EventPipe()
    monkeypatch.setattr(sleep_module, "pause", lambda _seconds: pipe.interrupt())

    with pytest.raises(ExternalCallInterruptedError):
        sleep(SleepContext(pipe=pipe, seconds=120))(SleepFor(), [])
