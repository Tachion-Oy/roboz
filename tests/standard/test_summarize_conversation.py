"""Tests for compactification's shared conversation summarizer."""

import json
import time
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime import EventPipe
from roboz.llm import LLMEndpoint, MockLLMEndpoint

from roboz.standard.tools.conversation_summarization import summarize as summarize_module
from roboz.standard.tools.conversation_summarization.summarize import (
    summarize_conversation_segment,
)


def _summarize(endpoint: MockLLMEndpoint, max_chars: int | None) -> str:
    return summarize_conversation_segment(
        endpoint=endpoint,
        system_prompt="Summarize.",
        instructions="Write a summary.",
        conversation="Some conversation.",
        max_chars=max_chars,
    )


def test_summarize_retries_until_response_fits_max_chars(monkeypatch):
    endpoint = MockLLMEndpoint([{"value": "x" * 50}, {"value": "short"}])
    calls = []
    original_call_llm_api = summarize_module.call_llm_api

    def capture_call(resolved_endpoint, messages, **kwargs):
        calls.append(list(messages))
        return original_call_llm_api(resolved_endpoint, messages, **kwargs)

    monkeypatch.setattr(summarize_module, "call_llm_api", capture_call)

    assert _summarize(endpoint, max_chars=10) == "short"
    assert endpoint.mock_responses == []
    assert "decoded `value` string must be at most 10 characters" in (
        calls[0][1].content
    )
    assert json.loads(calls[1][-2].content) == {"value": "x" * 50}


def test_summarize_returns_shortest_attempt_when_retries_exhaust(caplog):
    endpoint = MockLLMEndpoint(
        [{"value": "x" * 50}, {"value": "y" * 20}, {"value": "z" * 30}]
    )
    assert _summarize(endpoint, max_chars=10) == "y" * 20
    assert endpoint.mock_responses == []
    assert "Summary length attempts exhausted" in caplog.text


def test_summarize_without_max_chars_never_retries():
    endpoint = MockLLMEndpoint([{"value": "x" * 50}, {"value": "unused"}])
    assert _summarize(endpoint, max_chars=None) == "x" * 50
    assert len(endpoint.mock_responses) == 1


def test_summarize_uses_json_mode_for_real_endpoint(monkeypatch):
    endpoint = LLMEndpoint(
        client=object(),
        model_name="summary-model",
        api_name="test",
        output_format="text",
    )
    seen_endpoints = []

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
        client=SimpleNamespace(chat=SimpleNamespace(completions=BlockingCompletions())),
        model_name="blocking-model",
        api_name="test",
        stream=False,
    )
    runtime_pipe = EventPipe()
    runtime_pipe.initialize(dry_run=False, agent_name="librarian")
    errors: list[BaseException] = []

    def _summarize() -> None:
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

    caller = Thread(target=_summarize)
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
