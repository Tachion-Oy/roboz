"""Tests for compactification's shared conversation summarizer."""

import json
import time
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from roboshed.tools.compactification import summarize as summarize_module
from roboshed.tools.compactification import summarize_conversation_segment

from roboz.exceptions import ExternalCallCancelledError
from roboz.llm import LLMEndpoint, MockLLMEndpoint
from roboz.models import LIGHT_MAX_CHARS, NO_TRUNCATION
from roboz.runtime import LOG_DATA_ATTRIBUTE, EventPipe


class _UnusedModels:
    def list(self, **kwargs):
        raise AssertionError("Model discovery was not requested")


def _unused_close():
    raise AssertionError("Client closing was not requested")


def _summarize(
    endpoint: MockLLMEndpoint,
    max_chars: int | None,
    *,
    max_chars_tolerance_percent: float = 15.0,
) -> str:
    return summarize_conversation_segment(
        endpoint=endpoint,
        system_prompt="Summarize.",
        instructions="Write a summary.",
        conversation="Some conversation.",
        max_chars=max_chars,
        max_chars_tolerance_percent=max_chars_tolerance_percent,
    )


def test_summarize_retries_until_response_fits_max_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = MockLLMEndpoint([{"value": "x" * 50}, {"value": "short"}])
    calls: list[list[object]] = []
    original_call_llm_api = summarize_module.call_llm_api

    def capture_call(resolved_endpoint, messages, **kwargs):
        calls.append(list(messages))
        return original_call_llm_api(resolved_endpoint, messages, **kwargs)

    monkeypatch.setattr(summarize_module, "call_llm_api", capture_call)

    assert _summarize(endpoint, max_chars=10) == "short"
    assert endpoint.mock_responses == []
    first_prompt = calls[0][1]
    assert hasattr(first_prompt, "content")
    assert "requested budget for the decoded `value` string is 10 characters" in (
        first_prompt.content
    )
    assert "aim comfortably below the budget" in first_prompt.content
    assert "Do not count characters" in first_prompt.content
    feedback_response = calls[1][-2]
    feedback_prompt = calls[1][-1]
    assert hasattr(feedback_response, "content")
    assert hasattr(feedback_prompt, "content")
    assert json.loads(feedback_response.content) == {"value": "x" * 50}
    assert "Rewrite it substantially shorter" in feedback_prompt.content
    assert "roughly 400% over the requested budget" in feedback_prompt.content
    assert "50 characters" not in feedback_prompt.content
    assert "do not count characters" in feedback_prompt.content
    assert "comfortable headroom" in feedback_prompt.content


def test_summarize_preserves_conversation_larger_than_default_message_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = MockLLMEndpoint([{"value": "complete"}])
    calls: list[list[object]] = []
    original_call_llm_api = summarize_module.call_llm_api

    def capture_call(resolved_endpoint, messages, **kwargs):
        calls.append(list(messages))
        return original_call_llm_api(resolved_endpoint, messages, **kwargs)

    monkeypatch.setattr(summarize_module, "call_llm_api", capture_call)
    newest_tail = "newest conversation content"
    conversation = "x" * LIGHT_MAX_CHARS + newest_tail

    assert (
        summarize_conversation_segment(
            endpoint=endpoint,
            system_prompt="Summarize.",
            instructions="Write a summary.",
            conversation=conversation,
        )
        == "complete"
    )

    provider_prompt = calls[0][1]
    assert hasattr(provider_prompt, "truncation")
    assert hasattr(provider_prompt, "content")
    assert provider_prompt.truncation == NO_TRUNCATION
    assert len(provider_prompt.content) > LIGHT_MAX_CHARS
    assert newest_tail in provider_prompt.content


def test_summarize_accepts_response_within_default_tolerance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    endpoint = MockLLMEndpoint([{"value": "x" * 12}, {"value": "unused"}])

    with caplog.at_level("INFO"):
        assert _summarize(endpoint, max_chars=10) == "x" * 12

    assert len(endpoint.mock_responses) == 1
    assert "within tolerance" in caplog.text
    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("Summary accepted within tolerance")
    )
    assert getattr(record, LOG_DATA_ATTRIBUTE) == {
        "response_chars": 12,
        "requested_max_chars": 10,
        "accepted_max_chars": 12,
        "tolerance_percent": 15.0,
    }


def test_summarize_retries_one_character_above_tolerance() -> None:
    endpoint = MockLLMEndpoint([{"value": "x" * 13}, {"value": "short"}])

    assert _summarize(endpoint, max_chars=10) == "short"
    assert endpoint.mock_responses == []


def test_summarize_zero_tolerance_preserves_strict_limit() -> None:
    endpoint = MockLLMEndpoint([{"value": "x" * 11}, {"value": "short"}])

    assert _summarize(endpoint, max_chars=10, max_chars_tolerance_percent=0) == "short"
    assert endpoint.mock_responses == []


@pytest.mark.parametrize("tolerance", [-1.0, float("nan"), float("inf")])
def test_summarize_rejects_invalid_tolerance(tolerance: float) -> None:
    endpoint = MockLLMEndpoint([{"value": "unused"}])

    with pytest.raises(ValueError, match="max_chars_tolerance_percent"):
        _summarize(
            endpoint,
            max_chars=10,
            max_chars_tolerance_percent=tolerance,
        )

    assert len(endpoint.mock_responses) == 1


def test_summarize_returns_shortest_attempt_when_retries_exhaust(
    caplog: pytest.LogCaptureFixture,
) -> None:
    endpoint = MockLLMEndpoint(
        [{"value": "x" * 50}, {"value": "y" * 20}, {"value": "z" * 30}]
    )
    assert _summarize(endpoint, max_chars=10) == "y" * 20
    assert endpoint.mock_responses == []
    assert "Summary length attempts exhausted" in caplog.text
    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("Summary length attempts exhausted")
    )
    assert getattr(record, LOG_DATA_ATTRIBUTE)["accepted_max_chars"] == 12


def test_summarize_without_max_chars_never_retries() -> None:
    endpoint = MockLLMEndpoint([{"value": "x" * 50}, {"value": "unused"}])
    assert _summarize(endpoint, max_chars=None) == "x" * 50
    assert len(endpoint.mock_responses) == 1


def test_summarize_uses_json_mode_for_real_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = LLMEndpoint(
        client=SimpleNamespace(
            chat=object(), audio=object(), models=_UnusedModels(), close=_unused_close
        ),
        model_name="summary-model",
        api_name="test",
        output_format="text",
        extra_body={"reasoning": {"effort": "high"}},
    )
    seen_endpoints: list[LLMEndpoint] = []

    monkeypatch.setattr(summarize_module, "resolve_endpoint", lambda unused: endpoint)

    def fake_call_llm_api(resolved_endpoint, messages, **kwargs):
        del messages, kwargs
        seen_endpoints.append(resolved_endpoint)
        return '{"value": "summary"}', {}

    monkeypatch.setattr(summarize_module, "call_llm_api", fake_call_llm_api)

    result = _summarize(MockLLMEndpoint([]), max_chars=None)

    assert result == "summary"
    assert endpoint.output_format == "text"
    assert len(seen_endpoints) == 1
    assert seen_endpoints[0].output_format == "json"
    assert seen_endpoints[0].extra_body == {"reasoning": {"effort": "high"}}


def test_summarize_forwards_runtime_pipe_and_rechecks_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_pipe = EventPipe()
    runtime_pipe.initialize(dry_run=False, agent_name="librarian")

    def _complete_after_cancel(endpoint, messages, *, pipe, timeout_s):
        del endpoint, messages, timeout_s
        assert pipe is runtime_pipe
        runtime_pipe.cancel()
        return '{"value":"late summary"}', {}

    monkeypatch.setattr(summarize_module, "call_llm_api", _complete_after_cancel)

    with pytest.raises(ExternalCallCancelledError):
        summarize_conversation_segment(
            endpoint=MockLLMEndpoint([]),
            system_prompt="Summarize.",
            instructions="Write a summary.",
            conversation="Some conversation.",
            pipe=runtime_pipe,
        )


def test_summarize_abandons_blocking_provider_promptly_on_cancel() -> None:
    provider_started = Event()
    release_provider = Event()
    provider_finished = Event()

    class BlockingCompletions:
        def create(self, **kwargs):
            del kwargs
            provider_started.set()
            release_provider.wait(timeout=5)
            provider_finished.set()
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"value":"late summary"}')
                    )
                ],
            )

    endpoint = LLMEndpoint(
        client=SimpleNamespace(
            models=_UnusedModels(),
            close=_unused_close,
            chat=SimpleNamespace(completions=BlockingCompletions()),
        ),
        model_name="blocking-model",
        api_name="test",
        stream=False,
    )
    runtime_pipe = EventPipe()
    runtime_pipe.initialize(dry_run=False, agent_name="librarian")
    errors: list[BaseException] = []

    def _summarize_in_thread() -> None:
        try:
            summarize_conversation_segment(
                endpoint=endpoint,
                system_prompt="Summarize.",
                instructions="Write a summary.",
                conversation="Some conversation.",
                pipe=runtime_pipe,
            )
        except Exception as error:
            errors.append(error)

    caller = Thread(target=_summarize_in_thread)
    caller.start()
    assert provider_started.wait(timeout=1)

    started = time.monotonic()
    runtime_pipe.cancel()
    caller.join(timeout=1)
    elapsed = time.monotonic() - started

    try:
        assert not caller.is_alive()
        assert elapsed < 0.5
        assert len(errors) == 1
        assert isinstance(errors[0], ExternalCallCancelledError)
    finally:
        release_provider.set()
        assert provider_finished.wait(timeout=1)
