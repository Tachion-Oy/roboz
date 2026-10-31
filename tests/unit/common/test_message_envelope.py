import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from roboz import tool
from roboz.models import All, Empty, MessageKind, Message, Role, render_content
from roboz.models._serialization import get_finalized_message
from roboz.runtime.persistence import (
    logged_row_to_message,
    message_to_logged_row,
)

_AT = datetime(2026, 10, 31, 14, 2, 11, tzinfo=timezone(timedelta(hours=2)))


@pytest.fixture
def set_tz(monkeypatch: pytest.MonkeyPatch):
    def apply(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield apply
    monkeypatch.undo()
    time.tzset()


def test_tool_result_renders_caller_and_creation_time_ahead_of_payload() -> None:
    message = Message(
        role=Role.USER, content='{"value": "ok"}', caller="prompt_user", timestamp=_AT
    )

    assert render_content(message) == (
        '{"caller": "prompt_user", "timestamp": "2026-10-31T14:02:11+02:00", '
        '"value": "ok"}'
    )
    assert message.content == '{"value": "ok"}'


def test_rendering_keeps_creation_offset_after_timezone_changes(
    set_tz: Callable[[str], None],
) -> None:
    set_tz("Asia/Tokyo")
    message = Message(role=Role.USER, content="{}", caller="tool")
    created = render_content(message)

    set_tz("America/New_York")

    assert message.timestamp.utcoffset() == timedelta(hours=9)
    assert render_content(message) == created


@pytest.mark.parametrize(
    "message",
    [
        Message(role=Role.ASSISTANT, content='{"action": "stop"}'),
        Message(role=Role.USER, content='{"event": "deploy", "timestamp": "2026-01-01"}'),
        Message(role=Role.USER, content="plain prompt", caller="prompt_user"),
        Message(role=Role.USER, content="[1, 2]", caller="prompt_user"),
    ],
    ids=["assistant", "direct_json", "plain_text_result", "json_array_result"],
)
def test_content_without_eligible_envelope_is_verbatim(message: Message) -> None:
    assert render_content(message) == message.content


def test_bootstrap_results_show_caller_without_time() -> None:
    message = Message(
        role=Role.USER,
        content='{"value": "skill"}',
        caller="cli_tools",
        message_kind=MessageKind.AUTO_LOADED_SKILL,
    )

    assert render_content(message) == '{"caller": "cli_tools", "value": "skill"}'


@pytest.mark.parametrize("field", ["caller", "timestamp"])
def test_tool_output_models_cannot_declare_reserved_fields(field: str) -> None:
    Output = type("Output", (Empty,), {"__annotations__": {field: str}})

    def produce(input: Empty, messages: list[Message]) -> Output:  # type: ignore[valid-type]
        raise AssertionError("not called")

    with pytest.raises(ValueError, match=rf"reserved message fields: \['{field}'\]"):
        tool(produce)


@pytest.mark.parametrize("field", ["caller", "timestamp"])
def test_dynamic_tool_payloads_cannot_use_reserved_fields(field: str) -> None:
    with pytest.raises(ValueError, match=rf"reserved fields: \['{field}'\]"):
        get_finalized_message(All.model_validate({field: "x"}))


def test_persisted_rows_round_trip_caller_and_creation_offset() -> None:
    message = Message(role=Role.USER, content="{}", caller="tool", timestamp=_AT)
    row = message_to_logged_row(message, message_id="m", sequence=1)

    assert row.created_at == "2026-10-31T14:02:11.000+02:00"
    assert logged_row_to_message(row) == message
