import json
import time
from datetime import UTC, datetime

import pytest

from roboz.models import MessageKind, Message, Role, render_content
from roboz.runtime.persistence import (
    logged_row_to_message,
    message_to_logged_row,
)

_AT = datetime(2026, 10, 31, 12, 2, 11, tzinfo=UTC)


@pytest.fixture
def helsinki(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "Europe/Helsinki")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_tool_result_renders_caller_and_local_time_ahead_of_payload(helsinki) -> None:
    message = Message(
        role=Role.USER, content='{"value": "ok"}', caller="prompt_user", timestamp=_AT
    )

    assert render_content(message) == (
        '{"caller": "prompt_user", "timestamp": "2026-10-31T14:02:11+02:00", '
        '"value": "ok"}'
    )
    assert message.content == '{"value": "ok"}'


@pytest.mark.parametrize(
    "message",
    [
        Message(role=Role.ASSISTANT, content='{"action": "stop"}'),
        Message(role=Role.SYSTEM, content='{"value": "prompt"}'),
        Message(
            role=Role.USER,
            content='{"value": "memory"}',
            message_kind=MessageKind.STARTUP_CONTEXT,
        ),
        Message(role=Role.USER, content="plain prompt", caller="prompt_user"),
        Message(role=Role.USER, content="[1, 2]"),
    ],
    ids=["assistant", "system", "bootstrap", "plain_text", "json_array"],
)
def test_content_without_eligible_envelope_is_verbatim(message: Message) -> None:
    assert render_content(message) == message.content


def test_payload_fields_take_precedence_over_envelope() -> None:
    message = Message(
        role=Role.USER, content='{"caller": "own", "timestamp": "own"}', caller="tool"
    )

    assert json.loads(render_content(message)) == {"caller": "own", "timestamp": "own"}


def test_persisted_rows_round_trip_caller_and_timestamp() -> None:
    message = Message(role=Role.USER, content="{}", caller="tool")
    row = message_to_logged_row(message, message_id="m", sequence=1)

    assert logged_row_to_message(row) == message
