import json
import logging
import time
from threading import Event, Thread, Timer
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import BaseModel, ValidationError

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMAuthError,
    LLMCallCancelledError,
    LLMCallInterruptedError,
    LLMCallTimeoutError,
    LLMOutputFormatError,
    LLMProviderRequestError,
    LLMProviderUnavailableError,
    LLMRateLimitExceededError,
    LLMUnknownProviderError,
    NonexistentTool,
)
from roboz.llm import estimate_conversation_tokens
from roboz.llm._diagnostics import (
    classify_llm_provider_error,
    emit_llm_runtime_event,
)
from roboz.llm._retry import RetryState, run_with_retry
from roboz.llm.binding import LLMTelemetryDict
from roboz.llm.calls import _call_chat_completion, call_llm_api, call_transcription_api
from roboz.llm.completion import (
    _parse_response,
    _reprompt_on_error,
    decode_raw_JSON,
    get_completion,
)
from roboz.llm.endpoints import (
    LLMEndpoint,
    MockLLMEndpoint,
    MockProviderError,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
)
from roboz.llm.prompts import ACTION_FIX_PROMPT, JSON_FIX_PROMPT, PYDANTIC_FIX_PROMPT
from roboz.models import Empty, Message, Role, Str
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime._external import (
    ControlSignal,
    run_cancellable_external_call,
)
from roboz.runtime.events import MessageDeltaEvent, RuntimeEvent
from roboz.runtime import LOG_DATA_ATTRIBUTE
from roboz.runtime.observability import LifecycleKind, RuntimeEventLevel
from roboz.runtime.pipe import EventPipe
from roboz.runtime.sinks import PersistenceSink
from roboz.tooling.core import Tool

# --- decode_raw_JSON Tests ---


def test_decode_raw_JSON_valid():
    valid_json = '{"action": "test", "rationale": "because"}'
    assert decode_raw_JSON(valid_json) == {"action": "test", "rationale": "because"}


def test_decode_raw_JSON_with_markdown():
    json_md = '```json\n{"action": "test"}\n```'
    assert decode_raw_JSON(json_md) == {"action": "test"}

    json_md_2 = '´´´json{"action": "test"}´´´'
    assert decode_raw_JSON(json_md_2) == {"action": "test"}


def test_decode_raw_JSON_with_think_tags():
    response = '<think>some thought process</think>{"action": "test"}'
    assert decode_raw_JSON(response) == {"action": "test"}

    response_2 = '◁/think▷{"action": "test"}'
    assert decode_raw_JSON(response_2) == {"action": "test"}


def test_decode_raw_JSON_inner_triple_backticks_preserved():
    """Outer fences are stripped; ``` inside JSON string values must not be removed."""
    raw = '```json\n{"action": "x", "rationale": "has ``` inside"}\n```'
    assert decode_raw_JSON(raw) == {
        "action": "x",
        "rationale": "has ``` inside",
    }


def test_decode_raw_JSON_invalid():
    with pytest.raises(json.JSONDecodeError):
        decode_raw_JSON("not json")


@pytest.mark.parametrize("raw", ["[1, 2]", '"text"', "42", "true", "null"])
def test_decode_raw_JSON_rejects_non_object_values(raw: str) -> None:
    with pytest.raises(json.JSONDecodeError, match="Expected a JSON object"):
        decode_raw_JSON(raw)


def test_decode_raw_JSON_rejects_array_containing_an_object() -> None:
    with pytest.raises(json.JSONDecodeError, match="Expected a JSON object"):
        decode_raw_JSON('[{"action": "test"}]')


def test_decode_raw_JSON_discards_prose_before_object(caplog):
    caplog.set_level(logging.WARNING)
    raw = (
        "Let me verify the actual state of the dump files and any prior analysis "
        "before claiming things are pending. I'll check what's actually on disk."
        '{"action": "run_file_command", "rationale": "Checking the actual state '
        "of the Slack feed directory to verify whether the sweep count and extraction "
        'were already completed, since the user believes they were done.", "chain": '
        '"and", "file_commands": [{"command": "find", "argv": ["/home/tommi/'
        'Projects/TachionHub/readonly/safe-scripts/slack-scrape/slack-feed/", '
        '"-type", "f", "-name", "*.md"]}, {"command": "find", "argv": '
        '["/home/tommi/Projects/TachionHub/readonly/safe-scripts/slack-scrape/'
        'slack-feed/", "-type", "d"]}]}'
    )

    decoded = decode_raw_JSON(raw)

    assert decoded["action"] == "run_file_command"
    assert decoded["chain"] == "and"
    assert len(decoded["file_commands"]) == 2
    assert any("non-JSON prefix" in record.message for record in caplog.records)
    assert all("Let me verify" not in record.message for record in caplog.records)


def test_decode_raw_JSON_two_objects_logs_warning(caplog):
    """GPT-5 (and similar) sometimes emits two concatenated JSON objects; we keep the first."""
    caplog.set_level(logging.WARNING)
    raw = '{"action": "a", "rationale": "x"}{"action": "b", "rationale": "y"}'
    assert decode_raw_JSON(raw) == {"action": "a", "rationale": "x"}
    assert any("multiple JSON values" in r.message for r in caplog.records)


def test_decode_raw_JSON_trailing_non_json_no_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert decode_raw_JSON('{"a": 1}\nextra commentary') == {"a": 1}
    assert caplog.records == []


# --- _parse_response Tests ---


class MockInput(BaseModel):
    arg: str


def test_parse_response_valid():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput

    raw_response = '{"action": "mock_tool", "rationale": "ok", "arg": "value"}'

    result = _parse_response(raw_response, [mock_tool], None)
    assert result == {"action": "mock_tool", "rationale": "ok", "arg": "value"}


def test_parse_response_nonexistent_tool():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"

    raw_response = (
        '{"action": "other_tool", "rationale": "try another tool", "arg": "value"}'
    )

    with pytest.raises(NonexistentTool):
        _parse_response(raw_response, [mock_tool], None)


def test_parse_response_validation_error():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput

    # Missing 'arg'
    raw_response = '{"action": "mock_tool", "other": "value"}'

    with pytest.raises(ValidationError):
        _parse_response(raw_response, [mock_tool], None)


def test_parse_response_with_output_model_valid():
    raw_response = '{"arg": "value"}'

    result = _parse_response(raw_response, [], MockInput)
    assert result == {"arg": "value"}


def test_parse_response_with_output_model_validation_error():
    raw_response = '{"other": "value"}'

    with pytest.raises(ValidationError):
        _parse_response(raw_response, [], MockInput)


def test_parse_response_with_both_tools_and_output_model_raises():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"

    with pytest.raises(
        ValueError, match="active_tools and LlmOutputModel cannot both be given"
    ):
        _parse_response('{"arg": "value"}', [mock_tool], MockInput)


# --- _reprompt_on_error Tests ---


def test_reprompt_on_error_nonexistent_tool():
    error = NonexistentTool()
    messages = _reprompt_on_error("raw", error)
    assert len(messages) == 1
    assert messages[0].role == Role.ERROR
    assert ACTION_FIX_PROMPT in messages[0].content


def test_reprompt_on_error_json_error():
    error = json.JSONDecodeError("msg", "doc", 0)
    messages = _reprompt_on_error("raw", error)
    assert JSON_FIX_PROMPT in messages[0].content


def test_reprompt_on_error_validation_error():
    try:
        MockInput(arg=123)  # type: ignore as this is a type error
    except ValidationError as e:
        error = e

    messages = _reprompt_on_error("raw", error)
    assert PYDANTIC_FIX_PROMPT in messages[0].content


def test_reprompt_on_error_unhandled():
    error = ValueError("Random error")
    with pytest.raises(ValueError):
        _reprompt_on_error("raw", error)


# --- get_tool_call_completion Tests ---


def test_get_tool_call_completion_success():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput

    call_llm = Mock(
        return_value=(
            '{"action": "mock_tool", "rationale": "valid", "arg": "val"}',
            {},
        )
    )

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        active_tools=[mock_tool],
        call_llm_api=call_llm,
    )
    assert result == {"action": "mock_tool", "rationale": "valid", "arg": "val"}


def test_get_tool_call_completion_retry_success():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput

    # First call returns invalid JSON, second returns valid
    call_llm = Mock(
        side_effect=[
            ("invalid json", {}),
            (
                '{"action": "mock_tool", "rationale": "valid after retry", "arg": "val"}',
                {},
            ),
        ]
    )

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        active_tools=[mock_tool],
        call_llm_api=call_llm,
        tries=3,
    )
    assert result == {
        "action": "mock_tool",
        "rationale": "valid after retry",
        "arg": "val",
    }
    assert call_llm.call_count == 2


def test_get_tool_call_completion_retries_non_object_json() -> None:
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput
    call_llm = Mock(
        side_effect=[
            ("[]", {}),
            (
                '{"action": "mock_tool", "rationale": "valid", "arg": "val"}',
                {},
            ),
        ]
    )

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        active_tools=[mock_tool],
        call_llm_api=call_llm,
        tries=2,
    )

    assert result["action"] == "mock_tool"
    assert call_llm.call_count == 2


def test_get_tool_call_completion_retry_when_rationale_missing():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = MockInput

    # First response misses required Invoke.rationale; second is valid.
    call_llm = Mock(
        side_effect=[
            ('{"action": "mock_tool", "arg": "val"}', {}),
            (
                '{"action": "mock_tool", "rationale": "retry with rationale", "arg": "val"}',
                {},
            ),
        ]
    )

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        active_tools=[mock_tool],
        call_llm_api=call_llm,
        tries=3,
    )

    assert result == {
        "action": "mock_tool",
        "rationale": "retry with rationale",
        "arg": "val",
    }
    # Current behavior bug: this is 1 (no retry). Expected after fix: 2.
    assert call_llm.call_count == 2


def test_get_tool_call_completion_failure():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"

    call_llm = Mock(return_value=("invalid json", {}))

    with pytest.raises(LLMOutputFormatError):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            active_tools=[mock_tool],
            call_llm_api=call_llm,
            tries=2,
        )
    assert call_llm.call_count == 2


def test_get_completion_does_not_retry_on_cancellation() -> None:
    call_llm = Mock(side_effect=LLMCallCancelledError("cancelled"))
    with pytest.raises(LLMCallCancelledError):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            call_llm_api=call_llm,
            tries=3,
        )
    assert call_llm.call_count == 1


def test_get_completion_does_not_retry_on_interrupt() -> None:
    call_llm = Mock(side_effect=ExternalCallInterruptedError("interrupted"))
    with pytest.raises(ExternalCallInterruptedError):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            call_llm_api=call_llm,
            tries=3,
        )
    assert call_llm.call_count == 1


def test_get_completion_persists_error_message_to_given_pipe(tmp_path):
    sink = PersistenceSink.for_path(tmp_path)
    pipe = EventPipe(event_sinks=(sink,))
    pipe.initialize(dry_run=False, agent_name="llm_errors")
    call_llm = Mock(return_value=("", {}))

    with pytest.raises(LLMOutputFormatError):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            call_llm_api=call_llm,
            tries=1,
            error_pipe=pipe,
        )

    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "error"
    assert "Previous message: ''" in data["messages"][0]["content"]


def test_get_tool_call_completion_with_output_model_success():
    call_llm = Mock(return_value=('{"arg": "val"}', {}))

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        call_llm_api=call_llm,
        LlmOutputModel=MockInput,
    )
    assert result == {"arg": "val"}
    assert call_llm.call_count == 1


def test_get_tool_call_completion_with_output_model_retry_success():
    call_llm = Mock(side_effect=[("invalid json", {}), ('{"arg": "val"}', {})])

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        call_llm_api=call_llm,
        LlmOutputModel=MockInput,
        tries=3,
    )
    assert result == {"arg": "val"}
    assert call_llm.call_count == 2


def test_get_completion_can_reprompt_without_echoing_large_invalid_response():
    invalid = "plain markdown " * 1_000
    calls: list[list[Message]] = []

    def call_llm(messages: list[Message]) -> tuple[str, LLMTelemetryDict]:
        calls.append(messages)
        if len(calls) == 1:
            return invalid, {}
        return '{"arg": "val"}', {}

    result = get_completion(
        messages=[Message(role=Role.USER, content="hi")],
        call_llm_api=call_llm,
        LlmOutputModel=MockInput,
        tries=2,
        include_raw_response_in_reprompt=False,
    )

    assert result == {"arg": "val"}
    assert len(calls) == 2
    repair_message = calls[1][-1]
    assert repair_message.role is Role.ERROR
    assert "unparsable JSON" in repair_message.content
    assert invalid not in repair_message.content
    assert "Previous message:" not in repair_message.content


def test_get_tool_call_completion_with_both_tools_and_output_model_raises():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    call_llm = Mock(return_value=('{"arg": "val"}', {}))

    with pytest.raises(
        ValueError, match="active_tools and LlmOutputModel cannot both be given"
    ):
        get_completion(
            messages=[Message(role=Role.USER, content="hi")],
            active_tools=[mock_tool],
            call_llm_api=call_llm,
            LlmOutputModel=MockInput,
        )


def test_get_tool_call_completion_empty_messages():
    with pytest.raises(ValueError, match="Messages cannot be empty"):
        get_completion(messages=[], active_tools=[], call_llm_api=Mock())


def test_get_llm_call_completion_passes_all_messages_to_api() -> None:
    """get_llm_call_completion passes all messages to the API without reduction."""
    messages = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="user"),
    ]
    call_llm = Mock(return_value=('{"arg": "val"}', {}))

    result = get_completion(
        messages=messages,
        call_llm_api=call_llm,
        LlmOutputModel=MockInput,
    )

    assert result == {"arg": "val"}
    assert call_llm.call_count == 1
    called_messages = call_llm.call_args.args[0]
    assert len(called_messages) == 2
    assert called_messages[0].content == "sys"
    assert called_messages[1].content == "user"


def test_parse_response_scalar_extra_fields_raises():
    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = Str

    raw_response = '{"action": "mock_tool", "rationale": "Bad logic", "value": "val", "extra": "bad"}'

    with pytest.raises(ValidationError):
        _parse_response(raw_response, [mock_tool], None)


def test_parse_response_with_extra_allow():
    class ScalarAllow(Empty):
        value: Any

    mock_tool = MagicMock(spec=Tool)
    mock_tool.name = "mock_tool"
    mock_tool.InputModel = ScalarAllow

    raw_response = '{"action": "mock_tool", "rationale": "Bad logic", "value": "val", "extra": "bad"}'

    assert _parse_response(raw_response, [mock_tool], None)


# --- estimate_conversation_tokens ---


def test_estimate_conversation_tokens_empty() -> None:
    assert estimate_conversation_tokens([]) == 0


def test_estimate_conversation_tokens_longer_content_monotonic() -> None:
    short = [Message(role=Role.USER, content="hi")]
    long = [Message(role=Role.USER, content="hello world this is longer")]
    assert estimate_conversation_tokens(long) >= estimate_conversation_tokens(short)


def test_estimate_conversation_tokens_json_string_content() -> None:
    payload = json.dumps({"tool": "x", "flag": "wellness", "n": 42})
    messages = [Message(role=Role.USER, content=payload)]
    n = estimate_conversation_tokens(messages)
    assert n >= 0
    assert isinstance(n, int)


def test_estimate_conversation_tokens_drops_remove_severity_message() -> None:
    """Messages omitted from context (REMOVE) add no tokens; compare with baseline."""
    baseline = [
        Message(role=Role.USER, content="keep-me"),
    ]
    with_remove_tail = [
        Message(role=Role.USER, content="keep-me"),
        Message(role=Role.USER, content="should-not-count", truncation=NO_MESSAGE),
    ]
    assert estimate_conversation_tokens(
        with_remove_tail
    ) == estimate_conversation_tokens(baseline)


def test_call_llm_api_streams_mock_responses_when_delta_callback_given() -> None:
    endpoint = MockLLMEndpoint([{"action": "stop", "rationale": "", "value": "ok"}])
    deltas: list[str] = []

    content, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
    )

    assert "".join(deltas) == content
    assert json.loads(content) == {"action": "stop", "rationale": "", "value": "ok"}
    assert meta == {}


def test_call_llm_api_uses_pipe_cancellation_scope_by_default() -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="agent")
    pipe.cancel()

    with pytest.raises(LLMCallCancelledError):
        call_llm_api(
            MockLLMEndpoint([{"action": "stop", "rationale": "", "value": "ok"}]),
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )


def test_call_llm_api_rechecks_mock_cancellation_after_response_retrieval() -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="agent")
    endpoint = MockLLMEndpoint([{"action": "stop", "rationale": "", "value": "late"}])

    class CancelAfterPop(list[str | Exception]):
        def pop(self, index: int = -1) -> str | Exception:
            response = super().pop(index)
            pipe.cancel()
            return response

    endpoint.mock_responses = CancelAfterPop(endpoint.mock_responses)

    with pytest.raises(LLMCallCancelledError):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )


def test_call_llm_api_uses_pipe_interrupt_signal_by_default() -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="agent")
    pipe.interrupt()

    with pytest.raises(LLMCallInterruptedError):
        call_llm_api(
            MockLLMEndpoint([{"action": "stop", "rationale": "", "value": "ok"}]),
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )


def test_call_llm_api_interrupt_before_first_chunk_emits_no_deltas() -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="agent")
    deltas: list[str] = []
    provider_invoked = Event()
    allow_first_chunk = Event()
    attempted_first_chunk = Event()
    interrupted = Event()
    thread_errors: list[BaseException] = []

    class DelayedFirstChunkStream:
        def __iter__(self):
            assert allow_first_chunk.wait(timeout=1.0)
            attempted_first_chunk.set()
            yield _stream_chunk("late-first-chunk")

    class DelayedChatCompletions:
        def create(self, **kwargs: object):
            provider_invoked.set()
            return DelayedFirstChunkStream()

    endpoint = LLMEndpoint(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=DelayedChatCompletions())
        ),
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )

    def run_call() -> None:
        try:
            call_llm_api(
                endpoint,
                [Message(role=Role.USER, content="hello")],
                on_delta=deltas.append,
                pipe=pipe,
            )
        except LLMCallInterruptedError:
            interrupted.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            thread_errors.append(exc)

    thread = Thread(target=run_call, daemon=True)
    thread.start()

    assert provider_invoked.wait(timeout=1.0)
    pipe.interrupt()
    thread.join(timeout=1.0)
    assert interrupted.is_set()
    assert thread_errors == []

    # Mirror agent interrupt handling: flag clears before the abandoned worker exits.
    pipe.clear_interrupt()
    allow_first_chunk.set()
    assert attempted_first_chunk.wait(timeout=1.0)
    time.sleep(0.05)

    assert deltas == []


class _FakeChatCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object):
        self.calls.append(kwargs)
        return self.response


class _FakeChat:
    def __init__(self, response: object) -> None:
        self.completions = _FakeChatCompletions(response)


class _FakeLLMClient:
    def __init__(self, response: object) -> None:
        self.chat = _FakeChat(response)


def _stream_chunk(
    delta: str = "",
    usage: object | None = None,
    *,
    reasoning: str | None = None,
    reasoning_content: str | None = None,
    reasoning_details: list[object] | None = None,
    finish_reason: str | None = None,
    generation_id: str | None = None,
):
    return SimpleNamespace(
        id=generation_id,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=delta,
                    reasoning=reasoning,
                    reasoning_content=reasoning_content,
                    reasoning_details=reasoning_details,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def test_call_llm_api_streams_provider_chunks_and_usage() -> None:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=5)
    client = _FakeLLMClient(
        [
            _stream_chunk('{"arg":'),
            _stream_chunk('"ok"}'),
            _stream_chunk(usage=usage),
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        extra_body={
            "provider": {"sort": "throughput", "require_parameters": True},
            "reasoning": {"effort": "low"},
        },
    )
    deltas: list[str] = []

    content, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
    )

    assert content == '{"arg":"ok"}'
    assert deltas == ['{"arg":', '"ok"}']
    assert meta == {
        "endpoint": "fake",
        "model": "fake-model",
        "token_input": 3,
        "token_output": 5,
    }
    call = client.chat.completions.calls[0]
    assert call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}
    assert call["extra_body"] == {
        "provider": {"sort": "throughput", "require_parameters": True},
        "reasoning": {"effort": "low"},
    }


def test_stream_retains_object_usage_when_a_later_chunk_has_none() -> None:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=5)
    client = _FakeLLMClient(
        [
            _stream_chunk('{"arg":"ok"}'),
            _stream_chunk(usage=usage),
            _stream_chunk(),
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )

    content, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=lambda _: None,
    )

    assert content == '{"arg":"ok"}'
    assert meta["token_input"] == 3
    assert meta["token_output"] == 5


def test_stream_retains_dict_usage_when_a_later_chunk_has_none() -> None:
    usage = {
        "prompt_tokens": 2,
        "completion_tokens": 6,
    }
    client = _FakeLLMClient(
        [
            {"choices": [], "usage": usage},
            {"choices": [], "usage": None},
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )

    _, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=lambda _: None,
    )

    assert meta["token_input"] == 2
    assert meta["token_output"] == 6


def test_call_llm_api_closes_provider_stream_after_cancellation() -> None:
    signal = ControlSignal()

    class ClosableStream:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self):
            yield _stream_chunk("before-cancel")
            signal.set()
            yield _stream_chunk("after-cancel")

        def close(self) -> None:
            self.closed = True

    stream = ClosableStream()
    endpoint = LLMEndpoint(
        client=_FakeLLMClient(stream),
        model_name="fake-model",
        api_name="fake",
    )
    deltas: list[str] = []

    result = _call_chat_completion(
        endpoint,
        request={},
        on_delta=deltas.append,
        control_signals=(signal,),
    )

    assert result.content == "before-cancel"
    assert deltas == ["before-cancel"]
    assert stream.closed is True


def test_call_llm_api_persists_reasoning_only_stream_diagnostics_without_text(
    tmp_path,
) -> None:
    first_reasoning = "private analysis"
    second_reasoning = " before answering"
    usage = SimpleNamespace(
        prompt_tokens=3,
        completion_tokens=9,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=9),
    )
    client = _FakeLLMClient(
        [
            _stream_chunk(
                reasoning=first_reasoning,
                generation_id="generation-123",
            ),
            _stream_chunk(
                reasoning_content=second_reasoning,
                generation_id="generation-123",
            ),
            _stream_chunk(
                usage=usage,
                finish_reason="stop",
                generation_id="generation-123",
            ),
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="reasoning-model",
        api_name="fake",
        output_format="json",
    )
    sink = PersistenceSink.for_path(tmp_path)
    pipe = EventPipe(event_sinks=(sink,))
    pipe.initialize(dry_run=False, agent_name="agent")
    deltas: list[str] = []

    content, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
        pipe=pipe,
    )

    assert content == ""
    assert deltas == []
    assert meta.get("token_output") == 9
    assert sink.conversations_location is not None
    persisted = json.loads(sink.conversations_location.read_text(encoding="utf-8"))
    succeeded = next(
        event
        for event in persisted["runtime_events"]
        if event["category"] == "llm" and event["kind"] == "succeeded"
    )
    assert succeeded["data"] == {
        "call_id": succeeded["data"]["call_id"],
        "endpoint": "fake",
        "model": "reasoning-model",
        "duration_ms": succeeded["data"]["duration_ms"],
        "response_chars": 0,
        "token_input": 3,
        "token_output": 9,
        "reasoning_chars": len(first_reasoning + second_reasoning),
        "reasoning_chunks": 2,
        "reasoning_tokens": 9,
        "finish_reason": "stop",
        "provider_generation_id": "generation-123",
        "stream_chunks": 3,
        "content_chunks": 0,
    }
    serialized = json.dumps(persisted)
    assert first_reasoning not in serialized
    assert second_reasoning not in serialized


def test_call_llm_api_counts_one_reasoning_representation_per_stream_chunk() -> None:
    content = '{"arg":"ok"}'
    client = _FakeLLMClient(
        [
            _stream_chunk(
                reasoning="canonical",
                reasoning_content="duplicate alias",
                reasoning_details=[
                    {"type": "reasoning.text", "text": "duplicate detail"}
                ],
                generation_id="generation-456",
            ),
            _stream_chunk(
                content,
                reasoning_details=[
                    SimpleNamespace(type="reasoning.summary", summary="summary")
                ],
                finish_reason="stop",
                generation_id="generation-456",
            ),
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="reasoning-model",
        api_name="fake",
        output_format="json",
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")
    deltas: list[str] = []

    response, _ = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
        pipe=pipe,
    )

    succeeded = next(
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "succeeded"
    )
    assert response == content
    assert deltas == [content]
    assert succeeded.data is not None
    assert succeeded.data["reasoning_chars"] == len("canonicalsummary")
    assert succeeded.data["reasoning_chunks"] == 2
    assert succeeded.data["stream_chunks"] == 2
    assert succeeded.data["content_chunks"] == 1


def test_call_llm_api_collects_diagnostics_from_dict_stream_chunks() -> None:
    hidden = "dict reasoning"
    usage = {
        "prompt_tokens": 2,
        "completion_tokens": 6,
        "completion_tokens_details": {"reasoning_tokens": 4},
    }
    content = '{"arg":"ok"}'
    client = _FakeLLMClient(
        [
            {
                "id": "generation-dict",
                "choices": [
                    {
                        "delta": {
                            "content": "",
                            "reasoning_details": [
                                {"type": "reasoning.text", "text": hidden}
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            },
            {
                "id": "generation-dict",
                "choices": [
                    {
                        "delta": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            },
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="reasoning-model",
        api_name="fake",
        output_format="json",
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")

    response, _ = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=lambda _: None,
        pipe=pipe,
    )

    succeeded = next(
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "succeeded"
    )
    assert response == content
    assert succeeded.data is not None
    assert succeeded.data["reasoning_chars"] == len(hidden)
    assert succeeded.data["reasoning_chunks"] == 1
    assert succeeded.data["reasoning_tokens"] == 4
    assert succeeded.data["finish_reason"] == "stop"
    assert succeeded.data["provider_generation_id"] == "generation-dict"
    assert hidden not in str(succeeded.data)


def test_interrupted_stream_cannot_emit_late_chunks_after_next_stream_starts() -> None:
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")

    first_old_delta_seen = Event()
    late_old_delta_seen = Event()
    allow_old_stream_to_continue = Event()
    call_finished = Event()
    call_errors: list[BaseException] = []

    class OldStream:
        def __iter__(self):
            yield _stream_chunk("old-before-interrupt")
            assert allow_old_stream_to_continue.wait(timeout=1.0)
            yield _stream_chunk("old-after-interrupt")

    endpoint = LLMEndpoint(
        client=_FakeLLMClient(OldStream()),
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )

    def on_delta(delta: str) -> None:
        pipe.emit_message_delta(delta)
        if delta == "old-before-interrupt":
            first_old_delta_seen.set()
        if delta == "old-after-interrupt":
            late_old_delta_seen.set()

    def call_old_stream() -> None:
        try:
            call_llm_api(
                endpoint,
                [Message(role=Role.USER, content="hello")],
                on_delta=on_delta,
                pipe=pipe,
            )
        except LLMCallInterruptedError:
            pass
        except BaseException as exc:  # noqa: BLE001 - surfaced by assertion below
            call_errors.append(exc)
        finally:
            call_finished.set()

    old_message_id = pipe.start_message()
    thread = Thread(target=call_old_stream, daemon=True)
    thread.start()

    assert first_old_delta_seen.wait(timeout=1.0)
    pipe.interrupt()
    assert call_finished.wait(timeout=1.0)
    assert call_errors == []

    pipe.clear_interrupt()
    next_message_id = pipe.start_message()
    pipe.emit_message_delta("new-stream")
    allow_old_stream_to_continue.set()
    thread.join(timeout=1.0)
    assert not late_old_delta_seen.wait(timeout=0.5)

    deltas = [event for event in events if isinstance(event, MessageDeltaEvent)]
    assert [(delta.delta, delta.message_id) for delta in deltas] == [
        ("old-before-interrupt", old_message_id),
        ("new-stream", next_message_id),
    ]


def test_stream_delta_gate_stays_closed_after_interrupt_signal_clears() -> None:
    pipe = EventPipe()
    pipe.initialize(dry_run=False, agent_name="agent")
    deltas: list[str] = []

    class InterruptingStream:
        def __iter__(self):
            yield _stream_chunk("before-interrupt")
            pipe.interrupt()
            yield _stream_chunk("during-interrupt")
            pipe.clear_interrupt()
            yield _stream_chunk("after-clear")

    endpoint = LLMEndpoint(
        client=_FakeLLMClient(InterruptingStream()),
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )

    _call_chat_completion(
        endpoint,
        request={},
        on_delta=deltas.append,
        control_signals=pipe.control_signals,
    )

    assert deltas == ["before-interrupt"]


def test_call_llm_api_falls_back_when_endpoint_stream_is_false() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=4),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"arg":"ok"}'))],
    )
    client = _FakeLLMClient(response)
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
        extra_body={"reasoning": {"effort": "high"}},
    )
    deltas: list[str] = []

    content, meta = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
    )

    assert content == '{"arg":"ok"}'
    assert deltas == []
    assert meta == {
        "endpoint": "fake",
        "model": "fake-model",
        "token_input": 2,
        "token_output": 4,
    }
    call = client.chat.completions.calls[0]
    assert "stream" not in call
    assert "stream_options" not in call
    assert call["extra_body"] == {"reasoning": {"effort": "high"}}


def test_call_llm_api_omits_extra_body_when_unconfigured() -> None:
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    )
    client = _FakeLLMClient(response)
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        stream=False,
    )

    call_llm_api(endpoint, [Message(role=Role.USER, content="hello")])

    assert "extra_body" not in client.chat.completions.calls[0]


def test_call_llm_api_collects_non_streaming_reasoning_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hidden = "non-streaming private analysis"
    content = '{"arg":"ok"}'
    response = SimpleNamespace(
        id="generation-non-streaming",
        usage=SimpleNamespace(
            prompt_tokens=2,
            completion_tokens=7,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=hidden,
                ),
            )
        ],
    )
    endpoint = LLMEndpoint(
        client=_FakeLLMClient(response),
        model_name="reasoning-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")

    with caplog.at_level(logging.INFO, logger="roboz.llm._diagnostics"):
        returned, _ = call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )

    succeeded = next(
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "succeeded"
    )
    assert returned == content
    assert succeeded.data is not None
    assert succeeded.data["reasoning_chars"] == len(hidden)
    assert succeeded.data["reasoning_chunks"] == 1
    assert succeeded.data["reasoning_tokens"] == 5
    assert succeeded.data["finish_reason"] == "stop"
    assert succeeded.data["provider_generation_id"] == "generation-non-streaming"
    assert succeeded.data["stream_chunks"] is None
    assert succeeded.data["content_chunks"] is None
    assert hidden not in str(succeeded.data)
    assert succeeded.message == "LLM call succeeded: fake/reasoning-model"
    assert any(
        record.getMessage()
        == (
            "LLM call succeeded: fake/reasoning-model "
            "(tokens_in=2, tokens_out=7, reasoning_tokens=5)"
        )
        for record in caplog.records
    )


def test_cancellable_external_call_stops_waiting_on_cancel() -> None:
    signal = ControlSignal()
    timer = Timer(0.05, signal.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ExternalCallCancelledError):
            run_cancellable_external_call(
                lambda: time.sleep(1.0),
                control_signals=(signal,),
                timeout_s=2.0,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 0.5


def test_emit_llm_runtime_event_does_not_write_a_python_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipe = MagicMock(spec=EventPipe)

    with caplog.at_level(logging.DEBUG, logger="roboz.llm._diagnostics"):
        emit_llm_runtime_event(
            pipe,
            kind=LifecycleKind.SUCCEEDED,
            level=RuntimeEventLevel.INFO,
            message="LLM call succeeded: fake/model",
            data={"token_input": 2, "token_output": 4},
        )

    pipe.emit_runtime_event.assert_called_once()
    assert caplog.records == []


def test_call_llm_api_emits_runtime_events_for_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=4),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"arg":"ok"}'))],
    )
    client = _FakeLLMClient(response)
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")

    with caplog.at_level(logging.INFO, logger="roboz.llm._diagnostics"):
        content, _ = call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )

    assert content == '{"arg":"ok"}'
    runtime_events = [event for event in events if isinstance(event, RuntimeEvent)]
    llm_events = [event for event in runtime_events if event.category == "llm"]
    assert [event.kind for event in llm_events] == ["started", "succeeded"]
    assert llm_events[0].data is not None
    assert llm_events[0].data["endpoint"] == "fake"
    assert llm_events[1].data is not None
    assert "duration_ms" in llm_events[1].data
    assert llm_events[1].data["response_chars"] == len('{"arg":"ok"}')
    assert llm_events[1].data["reasoning_chars"] == 0
    assert llm_events[1].data["reasoning_chunks"] == 0
    assert llm_events[1].data["reasoning_tokens"] is None
    assert llm_events[1].data["finish_reason"] is None
    assert llm_events[1].data["provider_generation_id"] is None
    assert llm_events[1].data["stream_chunks"] is None
    assert llm_events[1].data["content_chunks"] is None
    assert "response_preview" not in llm_events[1].data
    assert llm_events[1].message == "LLM call succeeded: fake/fake-model"
    assert any(
        record.getMessage()
        == "LLM call succeeded: fake/fake-model (tokens_in=2, tokens_out=4)"
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("usage", "expected_message"),
    [
        (
            SimpleNamespace(prompt_tokens=2),
            "LLM call succeeded: fake/fake-model (tokens_in=2)",
        ),
        (None, "LLM call succeeded: fake/fake-model"),
    ],
)
def test_call_llm_api_omits_unavailable_tokens_from_success_log(
    usage: object,
    expected_message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content='{ "arg": "ok" }'))],
    )
    endpoint = LLMEndpoint(
        client=_FakeLLMClient(response),
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )

    with caplog.at_level(logging.INFO, logger="roboz.llm._diagnostics"):
        call_llm_api(endpoint, [Message(role=Role.USER, content="hello")])

    assert any(record.getMessage() == expected_message for record in caplog.records)


def test_call_llm_api_logs_provider_error_as_safe_structured_metadata(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    class ProviderError(Exception):
        status_code = 401
        request_id = "request-123"

        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.body = {"error": {"message": "provider account key disabled"}}

    class FailingCompletions:
        def create(self, **kwargs: object):
            raise ProviderError("bad api key")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )
    sink = PersistenceSink.for_path(tmp_path)
    pipe = EventPipe(event_sinks=(sink,))
    pipe.initialize(dry_run=False, agent_name="agent")

    with (
        caplog.at_level(logging.DEBUG, logger="roboz.llm._diagnostics"),
        pytest.raises(LLMAuthError),
    ):
        call_llm_api(endpoint, [Message(role=Role.USER, content="hello")], pipe=pipe)

    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text(encoding="utf-8"))
    assert "bad api key" not in json.dumps(data)
    assert "provider account key disabled" not in json.dumps(data)
    assert data["messages"] == []
    llm_events = [
        event for event in data["runtime_events"] if event["category"] == "llm"
    ]
    assert [event["kind"] for event in llm_events] == [
        "started",
        "failed",
    ]
    assert llm_events[1]["data"]["error_kind"] == "auth"
    assert llm_events[1]["level"] == "warning"

    provider_records = [
        record
        for record in caplog.records
        if record.name == "roboz.llm._diagnostics"
        and record.getMessage().startswith("LLM provider attempt failed:")
    ]
    assert len(provider_records) == 1
    provider_record = provider_records[0]
    provider_data = getattr(provider_record, LOG_DATA_ATTRIBUTE)
    assert provider_data == {
        "operation": "chat",
        "endpoint": "fake",
        "model": "fake-model",
        "call_id": llm_events[0]["data"]["call_id"],
        "attempt": 1,
        "provider_error_type": "ProviderError",
        "status_code": 401,
        "request_id": "request-123",
    }
    assert "bad api key" not in caplog.text
    assert "provider account key disabled" not in caplog.text
    assert provider_record.exc_info is None


def test_call_llm_api_classifies_mock_provider_error_and_emits_runtime_event(
    tmp_path,
) -> None:
    endpoint = MockLLMEndpoint([MockProviderError("Invalid API key", status_code=401)])
    sink = PersistenceSink.for_path(tmp_path)
    pipe = EventPipe(event_sinks=(sink,))
    pipe.initialize(dry_run=False, agent_name="agent")

    with pytest.raises(LLMAuthError):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            pipe=pipe,
        )

    assert sink.conversations_location is not None
    data = json.loads(sink.conversations_location.read_text(encoding="utf-8"))
    assert data["messages"] == []
    llm_events = [
        event for event in data["runtime_events"] if event["category"] == "llm"
    ]
    assert [event["kind"] for event in llm_events] == ["started", "failed"]
    assert llm_events[1]["data"]["error_kind"] == "auth"
    assert llm_events[1]["level"] == "warning"


def test_classify_llm_provider_error_maps_generic_4xx_to_request_error() -> None:
    error = classify_llm_provider_error(
        MockProviderError("unsupported request", status_code=418),
        MockLLMEndpoint([]),
    )

    assert type(error) is LLMProviderRequestError


def test_classify_llm_provider_error_keeps_5xx_fatal_despite_message() -> None:
    error = classify_llm_provider_error(
        MockProviderError("authorization service failed", status_code=500),
        MockLLMEndpoint([]),
    )

    assert isinstance(error, LLMProviderUnavailableError)


def test_classify_llm_provider_error_uses_endpoint_error_types() -> None:
    class ProviderRateLimit(Exception): ...

    endpoint = LLMEndpoint(
        client=object(),
        model_name="fake-model",
        api_name="fake",
        rate_limit_error=ProviderRateLimit,
    )

    error = classify_llm_provider_error(ProviderRateLimit("slow down"), endpoint)

    assert isinstance(error, LLMRateLimitExceededError)


class _FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object):
        self.calls.append(kwargs)
        return type("Result", (), {"text": "  hello world  "})()


class _FakeAudio:
    def __init__(self) -> None:
        self.transcriptions = _FakeTranscriptions()


class _FakeClient:
    def __init__(self) -> None:
        self.audio = _FakeAudio()


def test_call_transcription_api_calls_provider_and_strips_text() -> None:
    client = _FakeClient()
    endpoint = TranscriptionEndpoint(
        client=client,
        model_name="whisper-test",
        api_name="test-provider",
        language="en",
    )

    text = call_transcription_api(
        endpoint, b"audio-bytes", filename="clip.webm", content_type="audio/webm"
    )

    assert text == "hello world"
    (call,) = client.audio.transcriptions.calls
    assert call["file"] == ("clip.webm", b"audio-bytes", "audio/webm")
    assert call["model"] == "whisper-test"
    assert call["language"] == "en"
    assert call["temperature"] == 0.0


def test_call_transcription_api_uses_mock_transcription_endpoint() -> None:
    endpoint = MockTranscriptionEndpoint(["  first transcript  "])

    text = call_transcription_api(
        endpoint,
        b"audio-bytes",
        filename="clip.webm",
        content_type="audio/webm",
    )

    assert text == "first transcript"


# --- run_with_retry Tests ---


def test_run_with_retry_succeeds_without_logging_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    attempts: list[int] = []
    secret = "provider-secret-value"

    def flaky(state: RetryState) -> str:
        nonlocal calls
        calls += 1
        attempts.append(state.attempt)
        if calls < 3:
            raise LLMRateLimitExceededError(secret)
        return "ok"

    with caplog.at_level(logging.WARNING, logger="roboz.llm._retry"):
        result = run_with_retry(
            flaky,
            retryable_errors=(LLMRateLimitExceededError,),
            max_attempts=5,
            base_delay_s=0.01,
            max_delay_s=0.01,
        )

    assert result == "ok"
    assert calls == 3
    assert attempts == [1, 2, 3]
    assert "error_type=LLMRateLimitExceededError" in caplog.text
    assert secret not in caplog.text


def test_run_with_retry_exhausts_max_attempts_then_raises() -> None:
    calls = 0

    def always_fails(state: RetryState) -> str:
        nonlocal calls
        calls += 1
        raise LLMRateLimitExceededError("still slow")

    with pytest.raises(LLMRateLimitExceededError):
        run_with_retry(
            always_fails,
            retryable_errors=(LLMRateLimitExceededError,),
            max_attempts=3,
            base_delay_s=0.01,
            max_delay_s=0.01,
        )

    assert calls == 3


def test_run_with_retry_propagates_non_retryable_error_immediately() -> None:
    calls = 0

    def fails_with_auth_error(state: RetryState) -> str:
        nonlocal calls
        calls += 1
        raise LLMAuthError("bad key")

    with pytest.raises(LLMAuthError):
        run_with_retry(
            fails_with_auth_error,
            retryable_errors=(LLMRateLimitExceededError,),
            max_attempts=5,
            base_delay_s=0.01,
            max_delay_s=0.01,
        )

    assert calls == 1


def test_run_with_retry_does_not_retry_after_output() -> None:
    calls = 0

    def fails_after_output(state: RetryState) -> str:
        nonlocal calls
        calls += 1
        state.mark_output_emitted()
        raise LLMRateLimitExceededError("slow down after output")

    with pytest.raises(LLMRateLimitExceededError):
        run_with_retry(
            fails_after_output,
            retryable_errors=(LLMRateLimitExceededError,),
            max_attempts=5,
            base_delay_s=0.01,
            max_delay_s=0.01,
        )

    assert calls == 1


def test_run_with_retry_can_retry_after_output_with_callback() -> None:
    calls = 0
    retries: list[tuple[int, int, bool]] = []

    def flaky_after_output(state: RetryState) -> str:
        nonlocal calls
        calls += 1
        state.mark_output_emitted()
        if calls == 1:
            raise LLMRateLimitExceededError("slow down after output")
        return "ok"

    def prepare_retry(
        _error: Exception, attempt: int, limit: int, output_emitted: bool
    ) -> bool:
        retries.append((attempt, limit, output_emitted))
        return True

    result = run_with_retry(
        flaky_after_output,
        retryable_errors=(LLMRateLimitExceededError,),
        max_attempts=3,
        base_delay_s=0.01,
        max_delay_s=0.01,
        prepare_retry=prepare_retry,
    )

    assert result == "ok"
    assert calls == 2
    assert retries == [(1, 3, True)]


def test_run_with_retry_backoff_is_cancellation_aware() -> None:
    signal = ControlSignal()
    timer = Timer(0.05, signal.set)
    timer.start()

    def always_fails(state: RetryState) -> str:
        raise LLMRateLimitExceededError("slow down")

    started = time.monotonic()
    try:
        with pytest.raises(ExternalCallCancelledError):
            run_with_retry(
                always_fails,
                retryable_errors=(LLMRateLimitExceededError,),
                control_signals=(signal,),
                max_attempts=5,
                base_delay_s=1.0,
                max_delay_s=1.0,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 0.5


# --- Scripted fakes for call_llm_api / call_transcription_api retry tests ---


class _ScriptedChatCompletions:
    """Fake chat.completions.create; pops one scripted entry per call.

    Each entry is either an Exception (raised) or a response/stream object
    (returned), mirroring MockLLMEndpoint's own scripted-response idiom.
    """

    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object):
        self.calls.append(kwargs)
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry


class _ScriptedChat:
    def __init__(self, script: list[object]) -> None:
        self.completions = _ScriptedChatCompletions(script)


class _ScriptedLLMClient:
    def __init__(self, script: list[object]) -> None:
        self.chat = _ScriptedChat(script)


class _ScriptedTranscriptions:
    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object):
        self.calls.append(kwargs)
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry


class _ScriptedAudio:
    def __init__(self, script: list[object]) -> None:
        self.transcriptions = _ScriptedTranscriptions(script)


class _ScriptedTranscriptionClient:
    def __init__(self, script: list[object]) -> None:
        self.audio = _ScriptedAudio(script)


def _completion_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def _transcription_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


# --- call_llm_api retry Tests (non-streaming) ---


def test_call_llm_api_retries_transient_rate_limit_then_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ProviderRateLimit(Exception):
        status_code = 429

    client = _ScriptedLLMClient(
        [ProviderRateLimit("slow down"), _completion_response('{"arg":"ok"}')]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )

    with caplog.at_level(logging.DEBUG):
        content, _ = call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert content == '{"arg":"ok"}'
    assert len(client.chat.completions.calls) == 2
    external_records = [
        record
        for record in caplog.records
        if record.name == "roboz.runtime._external"
    ]
    assert external_records == []
    llm_records = [
        record for record in caplog.records if record.name == "roboz.llm._diagnostics"
    ]
    assert (
        sum(
            record.getMessage().startswith("LLM call started:")
            for record in llm_records
        )
        == 1
    )
    assert (
        sum(
            record.getMessage().startswith("LLM call retrying:")
            for record in llm_records
        )
        == 1
    )
    assert (
        sum(
            record.getMessage().startswith("LLM call succeeded:")
            for record in llm_records
        )
        == 1
    )


def test_call_llm_api_logs_each_failed_provider_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ProviderRateLimit(Exception):
        status_code = 429

    client = _ScriptedLLMClient(
        [ProviderRateLimit("slow down"), ProviderRateLimit("still slow")]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )

    with (
        caplog.at_level(logging.DEBUG, logger="roboz.llm._diagnostics"),
        pytest.raises(LLMRateLimitExceededError),
    ):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            max_attempts=2,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert len(client.chat.completions.calls) == 2
    provider_records = [
        record
        for record in caplog.records
        if record.name == "roboz.llm._diagnostics"
        and record.getMessage().startswith("LLM provider attempt failed:")
    ]
    provider_data = [
        getattr(record, LOG_DATA_ATTRIBUTE) for record in provider_records
    ]
    assert len(provider_data) == 2
    assert [data["attempt"] for data in provider_data] == [1, 2]
    call_ids = {data["call_id"] for data in provider_data}
    assert len(call_ids) == 1
    assert "slow down" not in caplog.text
    assert "still slow" not in caplog.text


def test_call_llm_api_does_not_retry_non_retryable_error() -> None:
    class ProviderAuthError(Exception):
        status_code = 401

    client = _ScriptedLLMClient(
        [ProviderAuthError("bad key"), _completion_response('{"arg":"ok"}')]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
        stream=False,
    )

    with pytest.raises(LLMAuthError):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            max_attempts=3,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert len(client.chat.completions.calls) == 1


def test_call_llm_api_mock_endpoint_bypasses_retry() -> None:
    endpoint = MockLLMEndpoint(
        [
            MockProviderError("slow down", status_code=429),
            {"arg": "ok"},
        ]
    )

    with pytest.raises(LLMRateLimitExceededError):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            max_attempts=3,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    # Second scripted response was never consumed -- the mock path never retries.
    assert endpoint.mock_responses == [json.dumps({"arg": "ok"})]


# --- call_llm_api retry Tests (streaming) ---


def test_call_llm_api_streaming_retries_before_first_chunk() -> None:
    class ProviderUnavailable(Exception):
        status_code = 503

    client = _ScriptedLLMClient(
        [
            ProviderUnavailable("overloaded"),
            [_stream_chunk('{"arg":'), _stream_chunk('"ok"}')],
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )
    deltas: list[str] = []

    content, _ = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=deltas.append,
        retry_base_delay_s=0.01,
        retry_max_delay_s=0.01,
    )

    assert content == '{"arg":"ok"}'
    assert deltas == ['{"arg":', '"ok"}']
    assert len(client.chat.completions.calls) == 2


def test_call_llm_api_retry_discards_failed_attempt_reasoning_diagnostics() -> None:
    class ProviderUnavailable(Exception):
        status_code = 503

    class ReasoningThenFailureStream:
        def __iter__(self):
            yield _stream_chunk(reasoning="abandoned reasoning")
            raise ProviderUnavailable("overloaded")

    content = '{"arg":"ok"}'
    client = _ScriptedLLMClient(
        [
            ReasoningThenFailureStream(),
            [
                _stream_chunk(content, generation_id="successful-generation"),
                _stream_chunk(finish_reason="stop"),
            ],
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")

    response, _ = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=lambda _: None,
        pipe=pipe,
        retry_base_delay_s=0.01,
        retry_max_delay_s=0.01,
    )

    succeeded = next(
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "succeeded"
    )
    assert response == content
    assert len(client.chat.completions.calls) == 2
    assert succeeded.data is not None
    assert succeeded.data["reasoning_chars"] == 0
    assert succeeded.data["reasoning_chunks"] == 0
    assert succeeded.data["provider_generation_id"] == "successful-generation"
    assert succeeded.data["stream_chunks"] == 2
    assert succeeded.data["content_chunks"] == 1


def test_call_llm_api_streaming_does_not_retry_after_first_chunk() -> None:
    class ProviderUnavailable(Exception):
        status_code = 503

    class FlakyStream:
        def __iter__(self):
            yield _stream_chunk("partial")
            raise ProviderUnavailable("connection dropped mid-stream")

    client = _ScriptedLLMClient([FlakyStream()])
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )
    deltas: list[str] = []

    with pytest.raises(LLMProviderUnavailableError):
        call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            on_delta=deltas.append,
            max_attempts=3,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert deltas == ["partial"]
    assert len(client.chat.completions.calls) == 1


def test_call_llm_api_streaming_retries_after_output_when_superseded() -> None:
    class ProviderUnavailable(Exception):
        status_code = 503

    class FlakyStream:
        def __iter__(self):
            yield _stream_chunk("partial")
            raise ProviderUnavailable("connection dropped mid-stream")

    client = _ScriptedLLMClient(
        [
            FlakyStream(),
            [_stream_chunk('{"arg":'), _stream_chunk('"ok"}')],
        ]
    )
    endpoint = LLMEndpoint(
        client=client,
        model_name="fake-model",
        api_name="fake",
        output_format="json",
    )
    pipe = EventPipe()
    events: list[object] = []
    pipe.add_sink(events.append)
    pipe.initialize(dry_run=False, agent_name="agent")
    first_message_id = pipe.start_message()

    def restart_stream() -> None:
        pipe.start_message()

    content, _ = call_llm_api(
        endpoint,
        [Message(role=Role.USER, content="hello")],
        on_delta=pipe.emit_message_delta,
        pipe=pipe,
        start_replacement_stream=restart_stream,
        max_attempts=3,
        retry_base_delay_s=0.01,
        retry_max_delay_s=0.01,
    )

    deltas = [event for event in events if isinstance(event, MessageDeltaEvent)]
    retry_events = [
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "retrying"
    ]
    failure_events = [
        event
        for event in events
        if isinstance(event, RuntimeEvent) and event.kind == "failed"
    ]

    assert content == '{"arg":"ok"}'
    assert len(client.chat.completions.calls) == 2
    assert [event.delta for event in deltas] == ["partial", '{"arg":', '"ok"}']
    assert deltas[0].message_id == first_message_id
    assert deltas[1].message_id != first_message_id
    assert deltas[2].message_id == deltas[1].message_id
    assert len(retry_events) == 1
    assert retry_events[0].level == "info"
    assert retry_events[0].data is not None
    assert retry_events[0].data["attempt"] == 2
    assert retry_events[0].data["max_attempts"] == 3
    assert retry_events[0].data["partial_output_abandoned"] is True
    assert failure_events == []


# --- call_transcription_api timeout/retry Tests ---


def test_call_transcription_api_times_out_and_raises_llm_call_timeout_error() -> None:
    class SlowTranscriptions:
        def create(self, **kwargs: object):
            time.sleep(1.0)
            return _transcription_response("too slow")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SlowTranscriptions()))
    endpoint = TranscriptionEndpoint(
        client=client, model_name="whisper-test", api_name="test-provider"
    )

    with pytest.raises(LLMCallTimeoutError):
        call_transcription_api(
            endpoint,
            b"audio-bytes",
            filename="clip.webm",
            content_type="audio/webm",
            timeout_s=0.05,
            max_attempts=1,
        )


def test_call_transcription_api_timeout_is_not_retried() -> None:
    calls = 0

    class SlowTranscriptions:
        def create(self, **kwargs: object):
            nonlocal calls
            calls += 1
            time.sleep(1.0)
            return _transcription_response("too slow")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SlowTranscriptions()))
    endpoint = TranscriptionEndpoint(
        client=client, model_name="whisper-test", api_name="test-provider"
    )

    with pytest.raises(LLMCallTimeoutError):
        call_transcription_api(
            endpoint,
            b"audio-bytes",
            filename="clip.webm",
            content_type="audio/webm",
            timeout_s=0.05,
            max_attempts=3,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert calls == 1


def test_call_transcription_api_logs_safe_provider_metadata_before_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ProviderUnavailable(Exception):
        status_code = 503
        response = SimpleNamespace(
            headers={"x-request-id": "transcription-request-123"},
            text="provider overload response body",
        )

    client = _ScriptedTranscriptionClient(
        [ProviderUnavailable("overloaded"), _transcription_response("  hello world  ")]
    )
    endpoint = TranscriptionEndpoint(
        client=client, model_name="whisper-test", api_name="test-provider"
    )

    with caplog.at_level(logging.DEBUG, logger="roboz.llm._diagnostics"):
        text = call_transcription_api(
            endpoint,
            b"audio-bytes",
            filename="clip.webm",
            content_type="audio/webm",
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert text == "hello world"
    assert len(client.audio.transcriptions.calls) == 2
    provider_records = [
        record
        for record in caplog.records
        if record.name == "roboz.llm._diagnostics"
        and record.getMessage().startswith("LLM provider attempt failed:")
    ]
    assert len(provider_records) == 1
    provider_data = getattr(provider_records[0], LOG_DATA_ATTRIBUTE)
    assert provider_data["operation"] == "transcription"
    assert provider_data["endpoint"] == "test-provider"
    assert provider_data["model"] == "whisper-test"
    assert provider_data["attempt"] == 1
    assert provider_data["status_code"] == 503
    assert provider_data["request_id"] == "transcription-request-123"
    assert "overloaded" not in caplog.text
    assert "provider overload response body" not in caplog.text


def test_call_transcription_api_does_not_retry_non_retryable_error() -> None:
    class ProviderAuthError(Exception):
        status_code = 401

    client = _ScriptedTranscriptionClient(
        [ProviderAuthError("bad key"), _transcription_response("hello world")]
    )
    endpoint = TranscriptionEndpoint(
        client=client, model_name="whisper-test", api_name="test-provider"
    )

    with pytest.raises(LLMAuthError):
        call_transcription_api(
            endpoint,
            b"audio-bytes",
            filename="clip.webm",
            content_type="audio/webm",
            max_attempts=3,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.01,
        )

    assert len(client.audio.transcriptions.calls) == 1


# --- classify_llm_provider_error transcription-endpoint-safety Tests ---


def test_classify_llm_provider_error_works_for_transcription_endpoint() -> None:
    class ProviderAuthError(Exception):
        status_code = 401

    endpoint = TranscriptionEndpoint(
        client=object(), model_name="whisper-test", api_name="test-provider"
    )

    error = classify_llm_provider_error(ProviderAuthError("bad key"), endpoint)

    assert isinstance(error, LLMAuthError)


def test_classify_llm_provider_error_works_for_mock_transcription_endpoint() -> None:
    class ProviderAuthError(Exception):
        status_code = 401

    endpoint = MockTranscriptionEndpoint(["unused"])

    error = classify_llm_provider_error(ProviderAuthError("bad key"), endpoint)

    assert isinstance(error, LLMAuthError)


# --- classify_llm_provider_error transport-error Tests ---


def _fake_httpx_transport_error() -> Exception:
    """A stand-in for httpx.ReadError: an httpx-module exception whose MRO
    includes TransportError. Built without importing httpx so the test runs in
    roboz's own (httpx-free) environment."""

    class TransportError(Exception): ...

    class ReadError(TransportError): ...

    TransportError.__module__ = "httpx"
    ReadError.__module__ = "httpx"
    return ReadError("[Errno 104] Connection reset by peer")


def _endpoint() -> LLMEndpoint:
    return LLMEndpoint(client=object(), model_name="fake-model", api_name="fake")


def test_classify_llm_provider_error_treats_httpx_transport_error_as_unavailable() -> (
    None
):
    error = classify_llm_provider_error(_fake_httpx_transport_error(), _endpoint())

    assert isinstance(error, LLMProviderUnavailableError)


def test_classify_llm_provider_error_treats_connection_reset_errno_as_unavailable() -> (
    None
):
    error = classify_llm_provider_error(
        OSError(104, "Connection reset by peer"), _endpoint()
    )

    assert isinstance(error, LLMProviderUnavailableError)


def test_classify_llm_provider_error_inspects_wrapped_oserror_cause() -> None:
    wrapped = RuntimeError("stream failed")
    wrapped.__cause__ = OSError(32, "Broken pipe")

    error = classify_llm_provider_error(wrapped, _endpoint())

    assert isinstance(error, LLMProviderUnavailableError)


def test_classify_llm_provider_error_leaves_unrelated_error_unknown() -> None:
    error = classify_llm_provider_error(
        RuntimeError("weird provider glitch"), _endpoint()
    )

    assert isinstance(error, LLMUnknownProviderError)
