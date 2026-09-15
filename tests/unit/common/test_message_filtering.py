import json

import pytest

from roboz.models import NO_MESSAGE, Message, Role, filter_messages


def test_role_filter_preserves_order_identity_and_duplicates() -> None:
    system = Message(role=Role.SYSTEM, content="instructions")
    assistant = Message(role=Role.ASSISTANT, content="not JSON")
    user = Message(role=Role.USER, content="hello")
    history = [system, assistant, user, assistant]
    before = [message.model_dump() for message in history]

    result = filter_messages(iter(history), role=Role.ASSISTANT)

    assert len(result) == 2
    assert result[0] is assistant and result[1] is assistant
    assert [message.model_dump() for message in history] == before
    assert filter_messages(history, role=Role.SYSTEM) == [system]
    assert filter_messages(history, role=Role.ERROR) == []


def test_no_filters_returns_a_new_list_and_accepts_empty_history() -> None:
    history = [Message(role=Role.USER, content="hello")]

    result = filter_messages(history)

    assert result is not history
    assert result[0] is history[0]
    assert filter_messages(iter(())) == []


def test_action_caller_and_combined_filters_match_exact_fields() -> None:
    request = Message(role=Role.ASSISTANT, content='{"action": "review"}')
    result = Message(role=Role.USER, content='{"caller": "review"}')
    both = Message(
        role=Role.USER, content='{"action": "review", "caller": "review"}'
    )
    history = [request, result, both]

    assert filter_messages(history, action="review") == [request, both]
    assert filter_messages(history, caller="review") == [result, both]
    assert filter_messages(history, action="review", caller="review") == [both]
    assert filter_messages(history, role=Role.ASSISTANT, caller="review") == []
    assert filter_messages(
        history, role=Role.USER, action="review", caller="review"
    ) == [both]
    assert filter_messages(history, action="Review") == []
    assert filter_messages(history, caller="rev") == []


@pytest.mark.parametrize("field", ["action", "caller"])
@pytest.mark.parametrize(
    "content",
    [
        "plain text",
        '{"unfinished":',
        "[]",
        '"review"',
        "42",
        "null",
        "{}",
        '{"action": null, "caller": null}',
        '{"action": 42, "caller": ["review"]}',
        '{"nested": {"action": "review", "caller": "review"}}',
    ],
)
def test_unmatched_content_is_skipped(field: str, content: str) -> None:
    message = Message(role=Role.USER, content=content)
    filters = {field: "review"}

    assert filter_messages([message], **filters) == []
    assert filter_messages([message], role=Role.USER) == [message]


def test_empty_string_is_a_filter_and_hidden_results_remain_searchable() -> None:
    hidden = Message(
        role=Role.USER,
        content=json.dumps({"caller": "initialize_context", "value": "ready"}),
        truncation=NO_MESSAGE,
    )
    empty_action = Message(role=Role.ASSISTANT, content='{"action": ""}')

    assert filter_messages([hidden, empty_action], action="") == [empty_action]
    assert filter_messages([hidden], caller="initialize_context")[0] is hidden
    assert hidden.truncation == NO_MESSAGE
