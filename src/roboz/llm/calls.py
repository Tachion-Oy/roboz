# ruff: noqa: F403, F405
import logging
import time
from functools import partial
from threading import Event
from typing import Any, Callable, Literal, TypedDict, cast
from uuid import uuid4

from roboz.exceptions import *  # noqa: F403
from roboz.models import Message
from roboz.models._telemetry import LLMTelemetry
from roboz.runtime.observability import (
    LifecycleKind,
    RuntimeEventLevel,
)
from roboz.runtime._external import (
    ControlSignal,
    run_cancellable_external_call,
)
from roboz.runtime.pipe import EventPipe
from roboz.llm._retry import RetryState, run_with_retry
from roboz.models._schema import messages_scrubber
from roboz.llm.binding import (
    LLMTelemetryDict,
    resolve_endpoint,
    resolve_transcription_endpoint,
)
from roboz.llm._diagnostics import (
    classify_llm_provider_error,
    emit_llm_failure_event,
    emit_llm_runtime_event,
    error_kind,
    llm_event_data,
    log_llm_provider_error,
)
from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
)

logger = logging.getLogger(__name__)

_RETRYABLE_LLM_ERRORS: tuple[type[Exception], ...] = (
    LLMRateLimitExceededError,
    LLMProviderUnavailableError,
)


def _classify_call_error(
    error: Exception,
    endpoint: (
        LLMEndpoint
        | MockLLMEndpoint
        | TranscriptionEndpoint
        | MockTranscriptionEndpoint
    ),
) -> LLMError:
    """Map a raw external-call-boundary or provider exception to a typed LLMError."""
    if isinstance(error, ExternalCallInterruptedError):
        return LLMCallInterruptedError("LLM call was interrupted")
    if isinstance(error, ExternalCallCancelledError):
        return LLMCallCancelledError("LLM call was cancelled")
    if isinstance(error, ExternalCallTimeoutError):
        return LLMCallTimeoutError("LLM call timed out")
    return classify_llm_provider_error(error, endpoint)


def call_llm_api(
    endpoint: EndpointLike,
    messages: list[Message],
    messages_scrubber: Callable[[list[Message]], list[dict]] = messages_scrubber,
    on_delta: Callable[[str], None] | None = None,
    pipe: EventPipe | None = None,
    timeout_s: float | None = None,
    max_attempts: int = 3,
    retry_base_delay_s: float = 0.5,
    retry_max_delay_s: float = 4.0,
    start_replacement_stream: Callable[[], None] | None = None,
) -> tuple[str, LLMTelemetryDict]:
    """Make a validated, cancellable, retrying API call to a specific LLM endpoint.

    Retries only cover provider-signaled transient errors: ``LLMRateLimitExceededError``
    and ``LLMProviderUnavailableError``. A local ``timeout_s`` deadline is not retried --
    it propagates as ``LLMCallTimeoutError`` on first occurrence. A streaming attempt
    stops being retryable the moment its first delta has been delivered to ``on_delta``
    unless ``start_replacement_stream`` is supplied. Non-streaming calls and
    streaming failures before the first delta retry without this callback. When
    partial output exists, the callback must start a replacement stream that
    supersedes it before the next attempt emits deltas.

    ``timeout_s`` bounds each attempt, not the whole call: a call retried after a rate
    limit gets a fresh deadline for the next attempt.
    """
    endpoint = resolve_endpoint(endpoint)
    signals = pipe.control_signals if pipe is not None else (ControlSignal(),)
    call_id = str(uuid4())
    started = time.monotonic()
    is_mock = isinstance(endpoint, MockLLMEndpoint)
    event_data = llm_event_data(endpoint, call_id=call_id, timeout_s=timeout_s)
    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.STARTED,
        level=RuntimeEventLevel.INFO,
        message=f"LLM call started: {endpoint.api_name}/{endpoint.model_name}",
        data=event_data
        | {
            "message_count": len(messages),
            "message_chars": sum(len(message.content) for message in messages),
            "output_format": getattr(endpoint, "output_format", "mock"),
            "stream": getattr(endpoint, "stream", False),
        },
    )

    try:
        if is_mock:
            content, meta = _call_mock_llm_api(endpoint, on_delta, signals)
        else:
            request = _chat_completion_request(endpoint, messages, messages_scrubber)
            call_label = f"llm:{endpoint.api_name}/{endpoint.model_name}"
            retry_label = f"{call_label} call_id={call_id}"
            llm_attempt = partial(
                _run_llm_attempt,
                endpoint=endpoint,
                request=request,
                on_delta=on_delta,
                control_signals=signals,
                timeout_s=timeout_s,
                call_label=call_label,
                call_id=call_id,
            )
            prepare_llm_retry = partial(
                _prepare_llm_retry,
                endpoint=endpoint,
                pipe=pipe,
                event_data=event_data,
                start_replacement_stream=start_replacement_stream,
            )
            content, meta = run_with_retry(
                llm_attempt,
                retryable_errors=_RETRYABLE_LLM_ERRORS,
                control_signals=signals,
                max_attempts=max_attempts,
                base_delay_s=retry_base_delay_s,
                max_delay_s=retry_max_delay_s,
                label=retry_label,
                prepare_retry=prepare_llm_retry,
            )
    except ExternalCallInterruptedError as e:
        error = LLMCallInterruptedError("LLM call was interrupted")
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except ExternalCallCancelledError as e:
        error = LLMCallCancelledError("LLM call was cancelled")
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except ExternalCallTimeoutError as e:
        error = LLMCallTimeoutError("LLM call timed out")
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except Exception as e:
        error = classify_llm_provider_error(e, endpoint)
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except BaseException:
        # Caller abandoned the wait boundary (e.g. KeyboardInterrupt injected by hub).
        raise

    duration_ms = round((time.monotonic() - started) * 1000)
    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.SUCCEEDED,
        level=RuntimeEventLevel.INFO,
        message=f"LLM call succeeded: {endpoint.api_name}/{endpoint.model_name}",
        data=event_data
        | {
            "duration_ms": duration_ms,
            "response_chars": len(content),
            "token_input": meta.get("token_input"),
            "token_output": meta.get("token_output"),
        },
    )
    return content, meta


def _call_mock_llm_api(
    endpoint: MockLLMEndpoint,
    on_delta: Callable[[str], None] | None,
    control_signals: tuple[ControlSignal, ...],
) -> tuple[str, LLMTelemetryDict]:
    _raise_for_mock_control_signal(control_signals)
    if not endpoint.mock_responses:
        raise RuntimeError("Mock script exhausted before stop")
    mock_response = endpoint.mock_responses.pop(0)
    _raise_for_mock_control_signal(control_signals)
    if isinstance(mock_response, Exception):
        raise mock_response
    if on_delta is not None:
        on_delta = _delta_gate(on_delta, control_signals)
        for delta in _iter_text_chunks(mock_response):
            on_delta(delta)
    return mock_response, cast(LLMTelemetryDict, {})


def _raise_for_mock_control_signal(
    control_signals: tuple[ControlSignal, ...],
) -> None:
    for signal in control_signals:
        try:
            signal.raise_if_set()
        except ExternalCallInterruptedError as e:
            raise LLMCallInterruptedError("LLM call was interrupted") from e
        except ExternalCallCancelledError as e:
            raise LLMCallCancelledError("LLM call was cancelled") from e


class ResponseFormat(TypedDict):
    type: Literal["json_object", "text"]


class ChaCompletionRequest(TypedDict):
    model: str
    messages: list[dict]
    temperature: float
    response_format: ResponseFormat


def _run_llm_attempt(
    retry_state: RetryState,
    *,
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
    on_delta: Callable[[str], None] | None,
    control_signals: tuple[ControlSignal, ...],
    timeout_s: float | None,
    call_label: str,
    call_id: str,
) -> tuple[str, LLMTelemetryDict]:
    attempt_abandoned = Event()
    call = partial(
        _call_chat_completion,
        endpoint,
        request,
        on_delta,
        control_signals,
        attempt_abandoned,
        retry_state,
    )
    try:
        return run_cancellable_external_call(
            call,
            control_signals=control_signals,
            timeout_s=timeout_s,
            label=call_label,
            call_id=call_id,
            attempt=retry_state.attempt,
            log_data={
                "endpoint": endpoint.api_name,
                "model": endpoint.model_name,
                "message_count": len(request["messages"]),
                "stream": bool(on_delta and endpoint.stream),
            },
        )
    except Exception as exc:
        attempt_abandoned.set()
        if not isinstance(
            exc,
            (
                ExternalCallInterruptedError,
                ExternalCallCancelledError,
                ExternalCallTimeoutError,
            ),
        ):
            log_llm_provider_error(
                endpoint,
                operation="chat",
                call_id=call_id,
                attempt=retry_state.attempt,
                error=exc,
            )
        raise _classify_call_error(exc, endpoint) from exc
    except BaseException:
        attempt_abandoned.set()
        raise


def _prepare_llm_retry(
    error: Exception,
    failed_attempt: int,
    attempt_limit: int,
    partial_output_emitted: bool,
    *,
    endpoint: LLMEndpoint,
    pipe: EventPipe | None,
    event_data: dict[str, Any],
    start_replacement_stream: Callable[[], None] | None,
) -> bool:
    if partial_output_emitted and start_replacement_stream is None:
        return False

    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.RETRYING,
        level=RuntimeEventLevel.INFO,
        message=(
            f"LLM call retrying: {endpoint.api_name}/{endpoint.model_name} "
            f"(attempt {failed_attempt + 1}/{attempt_limit})"
        ),
        data=event_data
        | {
            "attempt": failed_attempt + 1,
            "max_attempts": attempt_limit,
            "error_kind": error_kind(cast(LLMError, error)).value,
            "error_type": type(error).__name__,
            "partial_output_abandoned": partial_output_emitted,
        },
    )
    if partial_output_emitted:
        assert start_replacement_stream is not None
        start_replacement_stream()
    return True


def _call_chat_completion(
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
    on_delta: Callable[[str], None] | None,
    control_signals: tuple[ControlSignal, ...],
    call_abandoned: Event | None = None,
    retry_state: RetryState | None = None,
) -> tuple[str, LLMTelemetryDict]:
    for signal in control_signals:
        signal.raise_if_set()
    if on_delta is not None and endpoint.stream:
        return _call_streaming_llm_api(
            endpoint,
            request,
            _delta_gate(on_delta, control_signals, call_abandoned, retry_state),
            control_signals,
            call_abandoned,
        )
    return _call_non_streaming_llm_api(endpoint, request)


def _delta_gate(
    on_delta: Callable[[str], None],
    control_signals: tuple[ControlSignal, ...],
    call_abandoned: Event | None = None,
    retry_state: RetryState | None = None,
) -> Callable[[str], None]:
    def gate(delta: str) -> None:
        if _call_abandoned(call_abandoned) or _any_signal_set(control_signals):
            return
        if retry_state is not None:
            retry_state.mark_output_emitted()
        on_delta(delta)

    return gate


def _chat_completion_request(
    endpoint: LLMEndpoint,
    messages: list[Message],
    messages_scrubber: Callable[[list[Message]], list[dict]],
) -> ChaCompletionRequest:

    return {
        "model": endpoint.model_name,
        "messages": messages_scrubber(endpoint.message_mapper(messages)),
        "temperature": endpoint.temperature,
        "response_format": {
            "type": "json_object" if endpoint.output_format == "json" else "text"
        },
    }


def _call_non_streaming_llm_api(
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
) -> tuple[str, LLMTelemetryDict]:
    response = endpoint.client.chat.completions.create(**request)  # type:ignore
    meta = _telemetry_from_usage(
        endpoint=endpoint, usage=getattr(response, "usage", None)
    )
    if response.choices[0].message.content is None:  # type:ignore
        return "", meta
    return response.choices[0].message.content, meta  # type:ignore


def _call_streaming_llm_api(
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
    on_delta: Callable[[str], None],
    control_signals: tuple[ControlSignal, ...],
    call_abandoned: Event | None = None,
) -> tuple[str, LLMTelemetryDict]:
    stream_request = request | {
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    try:
        stream = endpoint.client.chat.completions.create(**stream_request)  # type:ignore
    except TypeError:
        stream_request.pop("stream_options", None)
        stream = endpoint.client.chat.completions.create(**stream_request)  # type:ignore

    chunks: list[str] = []
    usage = None
    try:
        for chunk in stream:
            if _call_abandoned(call_abandoned) or _any_signal_set(control_signals):
                break
            usage = (
                chunk.get("usage", usage)
                if isinstance(chunk, dict)
                else getattr(chunk, "usage", usage)
            )
            delta = _stream_delta_content(chunk)
            if not delta:
                continue
            chunks.append(delta)
            on_delta(delta)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - cleanup must not mask call outcome
                logger.warning("Failed to close LLM provider stream", exc_info=True)

    return "".join(chunks), _telemetry_from_usage(endpoint=endpoint, usage=usage)


def _any_signal_set(control_signals: tuple[ControlSignal, ...]) -> bool:
    return any(signal.is_set for signal in control_signals)


def _call_abandoned(call_abandoned: Event | None) -> bool:
    return call_abandoned is not None and call_abandoned.is_set()


def _stream_delta_content(chunk) -> str:
    choices = getattr(chunk, "choices", None)
    if choices is None and isinstance(chunk, dict):
        choices = chunk.get("choices")
    if not choices:
        return ""
    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is None and isinstance(first_choice, dict):
        delta = first_choice.get("delta")
    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


def _telemetry_from_usage(*, endpoint: LLMEndpoint, usage) -> LLMTelemetryDict:
    if isinstance(usage, dict):
        token_in = usage.get("prompt_tokens")
        token_out = usage.get("completion_tokens")
    else:
        token_in = getattr(usage, "prompt_tokens", None) if usage is not None else None
        token_out = (
            getattr(usage, "completion_tokens", None) if usage is not None else None
        )
    return cast(
        LLMTelemetryDict,
        LLMTelemetry.model_construct(
            endpoint=endpoint.api_name,
            model=endpoint.model_name,
            token_input=token_in,
            token_output=token_out,
        ).model_dump(mode="json", exclude_none=True),
    )


def _iter_text_chunks(text: str, *, chunk_size: int = 24):
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


def call_transcription_api(
    endpoint: TranscriptionEndpointLike,
    audio: bytes,
    *,
    filename: str,
    content_type: str,
    language: str | None = None,
    temperature: float | None = None,
    prompt: str | None = None,
    timeout_s: float | None = None,
    max_attempts: int = 3,
    retry_base_delay_s: float = 0.5,
    retry_max_delay_s: float = 4.0,
) -> str:
    """Make a validated, cancellable, retrying API call to a specific transcription endpoint.

    Retries only cover provider-signaled transient errors: ``LLMRateLimitExceededError``
    and ``LLMProviderUnavailableError``. A local ``timeout_s`` deadline is not retried --
    it propagates as ``LLMCallTimeoutError`` on first occurrence. ``timeout_s`` bounds
    each attempt, not the whole call -- see ``call_llm_api``.
    """
    endpoint = resolve_transcription_endpoint(endpoint)
    call_id = str(uuid4())
    started = time.monotonic()
    event_data = {
        "call_id": call_id,
        "endpoint": endpoint.api_name,
        "model": endpoint.model_name,
        "audio_bytes": len(audio),
    }
    emit_llm_runtime_event(
        None,
        kind=LifecycleKind.STARTED,
        level=RuntimeEventLevel.INFO,
        message=(
            f"Transcription call started: {endpoint.api_name}/{endpoint.model_name}"
        ),
        data=event_data,
    )
    if isinstance(endpoint, MockTranscriptionEndpoint):
        if not endpoint.mock_responses:
            raise RuntimeError("Mock transcription script exhausted")
        text = endpoint.mock_responses.pop(0).strip()
        emit_llm_runtime_event(
            None,
            kind=LifecycleKind.SUCCEEDED,
            level=RuntimeEventLevel.INFO,
            message=(
                f"Transcription call succeeded: {endpoint.api_name}/{endpoint.model_name}"
            ),
            data=event_data
            | {
                "duration_ms": round((time.monotonic() - started) * 1000),
                "response_chars": len(text),
            },
        )
        return text

    request: dict[str, object] = {
        "file": (filename, audio, content_type),
        "model": endpoint.model_name,
        "temperature": endpoint.temperature if temperature is None else temperature,
    }
    resolved_language = endpoint.language if language is None else language
    if resolved_language is not None:
        request["language"] = resolved_language
    resolved_prompt = endpoint.prompt if prompt is None else prompt
    if resolved_prompt is not None:
        request["prompt"] = resolved_prompt

    signals = (ControlSignal(),)
    call_label = f"transcription:{endpoint.api_name}/{endpoint.model_name}"

    def _attempt(retry_state: RetryState) -> str:
        try:
            response = run_cancellable_external_call(
                lambda: endpoint.client.audio.transcriptions.create(**request),
                control_signals=signals,
                timeout_s=timeout_s,
                label=call_label,
                call_id=call_id,
                attempt=retry_state.attempt,
                log_data={
                    "endpoint": endpoint.api_name,
                    "model": endpoint.model_name,
                    "audio_bytes": len(audio),
                },
            )
            return response.text.strip()
        except Exception as exc:
            if not isinstance(
                exc,
                (
                    ExternalCallInterruptedError,
                    ExternalCallCancelledError,
                    ExternalCallTimeoutError,
                ),
            ):
                log_llm_provider_error(
                    endpoint,
                    operation="transcription",
                    call_id=call_id,
                    attempt=retry_state.attempt,
                    error=exc,
                )
            raise _classify_call_error(exc, endpoint) from exc

    try:
        text = run_with_retry(
            _attempt,
            retryable_errors=_RETRYABLE_LLM_ERRORS,
            control_signals=signals,
            max_attempts=max_attempts,
            base_delay_s=retry_base_delay_s,
            max_delay_s=retry_max_delay_s,
            label=call_label,
        )
    except Exception as exc:
        error = _classify_call_error(exc, endpoint)
        emit_llm_failure_event(None, endpoint, call_id, started, error)
        raise
    emit_llm_runtime_event(
        None,
        kind=LifecycleKind.SUCCEEDED,
        level=RuntimeEventLevel.INFO,
        message=(
            f"Transcription call succeeded: {endpoint.api_name}/{endpoint.model_name}"
        ),
        data=event_data
        | {
            "duration_ms": round((time.monotonic() - started) * 1000),
            "response_chars": len(text),
        },
    )
    return text
