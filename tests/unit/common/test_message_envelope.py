from datetime import datetime, timedelta, timezone

from roboz.models import Message, MessageKind, Role, render_content
from roboz.runtime.persistence import logged_row_to_message, message_to_logged_row

_AT = datetime(2026, 10, 31, 14, 2, 11, tzinfo=timezone(timedelta(hours=2)))


def test_tool_result_renders_caller_and_time_ahead_of_payload() -> None:
    message = Message(role=Role.USER, content='{"value": "ok"}', caller="tool", timestamp=_AT)

    assert render_content(message) == (
        '{"caller": "tool", "timestamp": "2026-10-31T14:02:11+02:00", "value": "ok"}'
    )


def test_messages_without_caller_render_verbatim() -> None:
    message = Message(role=Role.USER, content='{"timestamp": "mine"}')

    assert render_content(message) == message.content


def test_bootstrap_results_omit_time() -> None:
    message = Message(
        role=Role.USER,
        content='{"value": "skill"}',
        caller="cli_tools",
        message_kind=MessageKind.AUTO_LOADED_SKILL,
    )

    assert render_content(message) == '{"caller": "cli_tools", "value": "skill"}'


def test_persisted_rows_round_trip_caller_and_timestamp() -> None:
    message = Message(role=Role.USER, content="{}", caller="tool", timestamp=_AT)
    row = message_to_logged_row(message, message_id="m", sequence=1)

    assert logged_row_to_message(row) == message
