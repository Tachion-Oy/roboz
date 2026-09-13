"""Public compactor runtime, budget, and dependency contracts."""

import json
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from roboshed.tools import CompactionContext
from roboshed.tools import get_compactify_messages_when_needed_tool
from roboshed.tools.compactification import (
    COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    COMPACTIFY_SYSTEM_PROMPT,
    compactify_messages_when_needed,
)
from roboshed.tools.compactification.compactify_messages import CompactionState

from roboz import All, Message, Role
from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMCallTimeoutError,
    LLMError,
)
from roboz.llm import LLMEndpoint, MockLLMEndpoint, estimate_conversation_tokens
from roboz.models import MessageKind
from roboz.runtime import EventPipe
from roboz.runtime.events import MessageEvent, RuntimeEvent


def _messages():
    return [Message(role=Role.USER, content="active work " * 400)]


def _response(value="continue"):
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"value": value}))
            )
        ],
    )


def _endpoint(create):
    return LLMEndpoint(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            models=SimpleNamespace(list=lambda **kwargs: pytest.fail("unexpected model check")),
            close=lambda: None,
        ),
        api_name="test",
        model_name="compaction",
        max_context_tokens=1000,
        stream=False,
    )


def _ctx(endpoint, pipe=None, timeout_s=None):
    return CompactionContext(
        state=CompactionState(),
        endpoint=endpoint,
        threshold_percent=80,
        system_prompt=COMPACTIFY_SYSTEM_PROMPT,
        skill_message=COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
        pipe=pipe,
        timeout_s=timeout_s,
    )


@pytest.mark.parametrize(
    "tokens,status", [(799, "ok"), (800, "compacted"), (801, "compacted")]
)
def test_threshold_boundary(tokens, status):
    endpoint = MockLLMEndpoint([{"value": "continue"}], max_context_tokens=1000)
    tool = get_compactify_messages_when_needed_tool(endpoint=endpoint)
    messages = [Message(role=Role.USER, content="u" * (tokens * 4))]
    output = tool(input=All(), messages=messages)
    assert output.status == status
    current_tokens = estimate_conversation_tokens(messages)
    assert output.to_compaction == str(800 - current_tokens)
    if status == "compacted":
        assert current_tokens < 800
        assert output.context == f"{current_tokens} / 1.0k ({current_tokens / 10:.0f}%)"
        assert json.loads(messages[-1].content)["percent_used_before"] == pytest.approx(
            tokens / 10
        )
    assert len(endpoint.mock_responses) == (1 if status == "ok" else 0)


def test_bootstrap_only_is_blocked_without_calling_provider():
    endpoint = MockLLMEndpoint([], max_context_tokens=1000)
    tool = get_compactify_messages_when_needed_tool(endpoint=endpoint)
    messages = [
        Message(
            role=Role.SYSTEM,
            content="s" * 4000,
            message_kind=MessageKind.SYSTEM_MESSAGE,
        )
    ]
    original = list(messages)
    output = tool(input=All(), messages=messages)
    assert output.status == "blocked"
    assert output.compactions == 0
    assert output.compaction_summary is None
    assert messages == original


@pytest.mark.parametrize("prefix_chars", [3100, 3200])
def test_prefix_and_payload_leave_no_summary_budget(prefix_chars):
    endpoint = MockLLMEndpoint([{"value": "unused"}], max_context_tokens=1000)
    ctx = _ctx(endpoint)
    ctx.state.count = 2
    messages = [
        Message(
            role=Role.SYSTEM,
            content="s" * prefix_chars,
            message_kind=MessageKind.SYSTEM_MESSAGE,
        ),
        *_messages(),
    ]
    original = list(messages)
    output = compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert output.status == "blocked"
    assert output.compaction_summary is None
    assert output.compactions == ctx.state.count == 2
    assert all(
        actual is previous for actual, previous in zip(messages, original, strict=True)
    )
    assert len(endpoint.mock_responses) == 1


def test_oversized_summary_retries_then_preserves_history_and_counter():
    calls = []

    def create(**request):
        calls.append(request)
        return _response("x" * 5000)

    ctx = _ctx(_endpoint(create))
    ctx.state.count = 2
    messages = _messages()
    original = list(messages)
    output = compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert len(calls) == 3
    assert output.status == "blocked"
    assert output.compaction_summary is None
    assert output.compactions == ctx.state.count == 2
    assert messages == original
    assert messages[0] is original[0]
    assert output.context == "1.2k / 1.0k (120%)"
    assert output.to_compaction == "0"


def test_summary_over_budget_retries_even_within_default_length_tolerance():
    endpoint = MockLLMEndpoint(
        [{"value": "x" * 3100}, {"value": "continue"}], max_context_tokens=1000
    )
    messages = _messages()
    output = get_compactify_messages_when_needed_tool(endpoint=endpoint)(
        input=All(), messages=messages
    )
    assert endpoint.mock_responses == []
    assert output.status == "compacted"
    assert output.compaction_summary == "continue"
    assert output.compactions == 1
    assert estimate_conversation_tokens(messages) < 800


def test_json_escaping_cannot_exceed_replacement_budget():
    endpoint = MockLLMEndpoint([{"value": "\U0001f600" * 300}], max_context_tokens=1000)
    ctx = _ctx(endpoint)
    ctx.state.count = 2
    messages = _messages()
    original = list(messages)
    output = compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert endpoint.mock_responses == []
    assert output.status == "blocked"
    assert output.compaction_summary is None
    assert output.compactions == ctx.state.count == 2
    assert messages == original
    assert messages[0] is original[0]


def test_repeated_compaction_uses_previous_summary_and_preserves_only_prefix():
    requests = []

    def create(**request):
        requests.append(request)
        return _response(f"summary-{len(requests)}")

    tool = get_compactify_messages_when_needed_tool(endpoint=_endpoint(create))
    bootstrap = Message(
        role=Role.SYSTEM, content="system", message_kind=MessageKind.SYSTEM_MESSAGE
    )
    messages = [bootstrap, *_messages()]
    first = tool(input=All(), messages=messages)
    messages.extend(_messages())
    messages.append(
        Message(
            role=Role.USER,
            content="late startup",
            message_kind=MessageKind.STARTUP_CONTEXT,
        )
    )
    second = tool(input=All(), messages=messages)
    assert (first.compactions, second.compactions) == (1, 2)
    assert messages[0] is bootstrap
    assert len(messages) == 2
    assert "summary-1" in requests[1]["messages"][1]["content"]
    assert "late startup" in requests[1]["messages"][1]["content"]
    payload = json.loads(messages[1].content)
    assert payload["caller"] == tool.name == "compactify_messages_when_needed"
    assert set(payload) == {
        "caller",
        "value",
        "summary_markdown",
        "percent_used_before",
        "threshold_percent",
    }
    assert payload["summary_markdown"] == second.compaction_summary == "summary-2"


def test_builder_counters_are_independent():
    endpoint = MockLLMEndpoint(
        [{"value": "one"}, {"value": "two"}], max_context_tokens=1000
    )
    first = get_compactify_messages_when_needed_tool(endpoint=endpoint)
    second = get_compactify_messages_when_needed_tool(endpoint=endpoint)
    assert first(input=All(), messages=_messages()).compactions == 1
    assert second(input=All(), messages=[]).compactions == 0
    assert second(input=All(), messages=_messages()).compactions == 1


def test_prompt_overrides_and_summarization_events_use_owning_pipe():
    events = []
    requests = []

    def create(**request):
        requests.append(request)
        return _response()

    pipe = EventPipe(event_sinks=[events.append])
    pipe.initialize(dry_run=False, agent_name="owner")
    tool = get_compactify_messages_when_needed_tool(
        endpoint=_endpoint(create),
        pipe=pipe,
        system_prompt="CUSTOM SYSTEM",
        skill_message="CUSTOM INSTRUCTIONS",
        timeout_s=1,
    )
    assert tool(input=All(), messages=_messages()).status == "compacted"
    assert "CUSTOM SYSTEM" in requests[0]["messages"][0]["content"]
    assert "CUSTOM INSTRUCTIONS" in requests[0]["messages"][1]["content"]
    assert any(
        isinstance(event, MessageEvent)
        and "CUSTOM INSTRUCTIONS" in event.message.content
        for event in events
    )
    runtime = [event for event in events if isinstance(event, RuntimeEvent)]
    assert runtime and all(event.agent_name == "owner" for event in runtime)


@pytest.mark.parametrize("field", ["threshold_percent", "timeout_s"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_budgets_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        get_compactify_messages_when_needed_tool(
            endpoint=MockLLMEndpoint([]), **{field: value}
        )


@pytest.mark.parametrize(
    "signal,error_type",
    [
        ("cancel", ExternalCallCancelledError),
        ("interrupt", ExternalCallInterruptedError),
    ],
)
@pytest.mark.parametrize("when", ["before", "completion"])
def test_control_signal_preserves_history_and_counter(signal, error_type, when):
    pipe = EventPipe()
    calls = []

    def create(**request):
        calls.append(request)
        getattr(pipe, signal)()
        return _response("late summary")

    ctx = _ctx(_endpoint(create), pipe)
    tool = compactify_messages_when_needed(ctx)
    messages = _messages()
    original = list(messages)
    if when == "before":
        getattr(pipe, signal)()
    with pytest.raises(error_type):
        tool(input=All(), messages=messages)
    assert bool(calls) == (when == "completion")
    assert messages == original
    assert ctx.state.count == 0


@pytest.mark.parametrize(
    "signal,error_type",
    [
        ("cancel", ExternalCallCancelledError),
        ("interrupt", ExternalCallInterruptedError),
        ("timeout", LLMCallTimeoutError),
    ],
)
def test_blocked_provider_can_be_abandoned_without_mutating_history(signal, error_type):
    started, release, finished = Event(), Event(), Event()

    def create(**request):
        started.set()
        release.wait(timeout=5)
        finished.set()
        return _response("late summary")

    # Timeout must also work for standalone use without an owning pipe.
    pipe = None if signal == "timeout" else EventPipe()
    ctx = _ctx(_endpoint(create), pipe, 0.1 if signal == "timeout" else None)
    tool = compactify_messages_when_needed(ctx)
    messages = _messages()
    original = list(messages)
    errors = []

    def invoke():
        try:
            tool(input=All(), messages=messages)
        except Exception as error:
            errors.append(error)

    caller = Thread(target=invoke)
    caller.start()
    try:
        assert started.wait(timeout=2)
        if pipe is not None:
            getattr(pipe, signal)()
        caller.join(timeout=2)
        assert not caller.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], error_type)
        assert not finished.is_set()
        assert messages == original
        assert ctx.state.count == 0
    finally:
        release.set()
        caller.join(timeout=2)
        assert finished.wait(timeout=2)
    assert messages == original
    assert ctx.state.count == 0


def test_provider_failure_preserves_history_and_counter():
    def create(**request):
        raise RuntimeError("provider failed")

    ctx = _ctx(_endpoint(create))
    messages = _messages()
    original = list(messages)
    with pytest.raises(LLMError):
        compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert messages == original
    assert ctx.state.count == 0


def test_endpoint_client_is_unused_by_binding_copying_and_inspection():
    calls = []

    def create(**request):
        calls.append(request)
        return _response()

    endpoint = _endpoint(create)
    tool = get_compactify_messages_when_needed_tool(endpoint=endpoint)
    assert tool.external_dependencies() == (endpoint,)
    assert tool.copy().external_dependencies()[0] is endpoint
    assert calls == []
    assert tool(input=All(), messages=_messages()).status == "compacted"
    assert len(calls) == 1


def test_counter_belongs_to_context_and_rebinding_and_copying_share_it():
    endpoint = MockLLMEndpoint([{"value": "continue"}] * 4, max_context_tokens=1000)
    context = CompactionContext(
        endpoint=endpoint,
        threshold_percent=80,
        system_prompt=COMPACTIFY_SYSTEM_PROMPT,
        skill_message=COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    )
    first = compactify_messages_when_needed(context)
    second = compactify_messages_when_needed(context)
    assert first(All(), _messages()).compactions == 1
    assert second(All(), _messages()).compactions == 2
    assert first.copy()(All(), _messages()).compactions == 3
    independent = compactify_messages_when_needed(_ctx(endpoint))
    assert independent(All(), _messages()).compactions == 1
