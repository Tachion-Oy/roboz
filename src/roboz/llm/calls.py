# ruff: noqa: F403, F405
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from threading import Event
from typing import Any, Final, Literal, NotRequired, TypedDict, cast
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
    LLMResponseDiagnostics,
    classify_llm_provider_error,
    emit_llm_failure_event,
    emit_llm_runtime_event,
    error_kind,
    llm_event_data,
    log_llm_call,
    log_llm_failure,
    log_llm_provider_error,
)
from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    RequestOptions,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
)

logger = logging.getLogger(__name__)

_RETRYABLE_LLM_ERRORS: Final[tuple[type[Exception], ...]] = (
    LLMRateLimitExceededError,
    LLMProviderUnavailableError,
)

_CHAT_OPERATION: Final[str] = "chat"
_TRANSCRIPTION_OPERATION: Final[str] = "transcription"
_MOCK_OUTPUT_FORMAT: Final[str] = "mock"
_JSON_OUTPUT_FORMAT: Final[str] = "json"
_JSON_OBJECT_RESPONSE_TYPE: Final[Literal["json_object"]] = "json_object"
_TEXT_RESPONSE_TYPE: Final[Literal["text"]] = "text"
_MESSAGE_COUNT_KEY: Final[str] = "message_count"
_MESSAGE_CHARS_KEY: Final[str] = "message_chars"
_OUTPUT_FORMAT_KEY: Final[str] = "output_format"
_STREAM_KEY: Final[str] = "stream"
_DURATION_MS_KEY: Final[str] = "duration_ms"
_RESPONSE_CHARS_KEY: Final[str] = "response_chars"
_TOKEN_INPUT_KEY: Final[str] = "token_input"
_TOKEN_OUTPUT_KEY: Final[str] = "token_output"
_REASONING_TOKENS_KEY: Final[str] = "reasoning_tokens"
_ATTEMPT_KEY: Final[str] = "attempt"
_MAX_ATTEMPTS_KEY: Final[str] = "max_attempts"
_ERROR_KIND_KEY: Final[str] = "error_kind"
_ERROR_TYPE_KEY: Final[str] = "error_type"
_PARTIAL_OUTPUT_ABANDONED_KEY: Final[str] = "partial_output_abandoned"
_CALL_ID_KEY: Final[str] = "call_id"
_ENDPOINT_KEY: Final[str] = "endpoint"
_MODEL_KEY: Final[str] = "model"
_AUDIO_BYTES_KEY: Final[str] = "audio_bytes"
_MESSAGES_FIELD: Final[str] = "messages"
_EXTRA_BODY_FIELD: Final[str] = "extra_body"
_STREAM_OPTIONS_FIELD: Final[str] = "stream_options"
_INCLUDE_USAGE_FIELD: Final[str] = "include_usage"
_USAGE_FIELD: Final[str] = "usage"
_CHOICES_FIELD: Final[str] = "choices"
_DELTA_FIELD: Final[str] = "delta"
_CONTENT_FIELD: Final[str] = "content"
_PROMPT_TOKENS_FIELD: Final[str] = "prompt_tokens"
_COMPLETION_TOKENS_FIELD: Final[str] = "completion_tokens"
_STREAM_CLOSE_METHOD: Final[str] = "close"
_FILE_FIELD: Final[str] = "file"
_MODEL_FIELD: Final[str] = "model"
_TEMPERATURE_FIELD: Final[str] = "temperature"
_LANGUAGE_FIELD: Final[str] = "language"
_PROMPT_FIELD: Final[str] = "prompt"
_TEXT_FIELD: Final[str] = "text"
_MILLISECONDS_PER_SECOND: Final[int] = 1_000
_TOKEN_SUMMARY_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("tokens_in", _TOKEN_INPUT_KEY),
    ("tokens_out", _TOKEN_OUTPUT_KEY),
    (_REASONING_TOKENS_KEY, _REASONING_TOKENS_KEY),
)


@dataclass(frozen=True)
class _ChatCompletionResult:
    content: str
    telemetry: LLMTelemetryDict
    diagnostics: LLMResponseDiagnostics


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
    started_message = f"LLM call started: {endpoint.api_name}/{endpoint.model_name}"
    started_data = event_data | {
        _MESSAGE_COUNT_KEY: len(messages),
        _MESSAGE_CHARS_KEY: sum(len(message.content) for message in messages),
        _OUTPUT_FORMAT_KEY: getattr(
            endpoint, _OUTPUT_FORMAT_KEY, _MOCK_OUTPUT_FORMAT
        ),
        _STREAM_KEY: getattr(endpoint, _STREAM_KEY, False),
    }
    log_llm_call(
        level=RuntimeEventLevel.INFO,
        message=started_message,
        data=started_data,
    )
    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.STARTED,
        level=RuntimeEventLevel.INFO,
        message=started_message,
        data=started_data,
    )

    try:
        if is_mock:
            result = _call_mock_llm_api(endpoint, on_delta, signals)
        else:
            request = _chat_completion_request(endpoint, messages, messages_scrubber)
            call_label = f"llm:{endpoint.api_name}/{endpoint.model_name}"
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
            result = run_with_retry(
                llm_attempt,
                retryable_errors=_RETRYABLE_LLM_ERRORS,
                control_signals=signals,
                max_attempts=max_attempts,
                base_delay_s=retry_base_delay_s,
                max_delay_s=retry_max_delay_s,
                label=call_label,
                prepare_retry=prepare_llm_retry,
            )
    except ExternalCallInterruptedError as e:
        error = LLMCallInterruptedError("LLM call was interrupted")
        log_llm_failure(endpoint, call_id, started, error)
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except ExternalCallCancelledError as e:
        error = LLMCallCancelledError("LLM call was cancelled")
        log_llm_failure(endpoint, call_id, started, error)
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except ExternalCallTimeoutError as e:
        error = LLMCallTimeoutError("LLM call timed out")
        log_llm_failure(endpoint, call_id, started, error)
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except Exception as e:
        error = classify_llm_provider_error(e, endpoint)
        log_llm_failure(endpoint, call_id, started, error)
        emit_llm_failure_event(pipe, endpoint, call_id, started, error)
        raise error from e
    except BaseException:
        # Caller abandoned the wait boundary (e.g. KeyboardInterrupt injected by hub).
        raise

    duration_ms = round((time.monotonic() - started) * 1000)
    succeeded_message = f"LLM call succeeded: {endpoint.api_name}/{endpoint.model_name}"
    succeeded_data = (
        event_data
        | {
            _DURATION_MS_KEY: duration_ms,
            _RESPONSE_CHARS_KEY: len(result.content),
            _TOKEN_INPUT_KEY: result.telemetry.get(_TOKEN_INPUT_KEY),
            _TOKEN_OUTPUT_KEY: result.telemetry.get(_TOKEN_OUTPUT_KEY),
        }
        | result.diagnostics.event_data()
    )
    token_summary = ", ".join(
        f"{label}={value}"
        for label, data_key in _TOKEN_SUMMARY_FIELDS
        if (value := succeeded_data.get(data_key)) is not None
    )
    log_message = (
        f"{succeeded_message} ({token_summary})" if token_summary else succeeded_message
    )
    log_llm_call(
        level=RuntimeEventLevel.INFO,
        message=log_message,
        data=succeeded_data,
    )
    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.SUCCEEDED,
        level=RuntimeEventLevel.INFO,
        message=succeeded_message,
        data=succeeded_data,
    )
    return result.content, result.telemetry


def _call_mock_llm_api(
    endpoint: MockLLMEndpoint,
    on_delta: Callable[[str], None] | None,
    control_signals: tuple[ControlSignal, ...],
) -> _ChatCompletionResult:
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
    return _ChatCompletionResult(
        content=mock_response,
        telemetry=cast(LLMTelemetryDict, {}),
        diagnostics=LLMResponseDiagnostics(),
    )


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
    extra_body: NotRequired[RequestOptions]


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
) -> _ChatCompletionResult:
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
                _ENDPOINT_KEY: endpoint.api_name,
                _MODEL_KEY: endpoint.model_name,
                _MESSAGE_COUNT_KEY: len(request[_MESSAGES_FIELD]),
                _STREAM_KEY: bool(on_delta and endpoint.stream),
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
                operation=_CHAT_OPERATION,
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

    retry_message = (
        f"LLM call retrying: {endpoint.api_name}/{endpoint.model_name} "
        f"(attempt {failed_attempt + 1}/{attempt_limit})"
    )
    retry_data = event_data | {
        _ATTEMPT_KEY: failed_attempt + 1,
        _MAX_ATTEMPTS_KEY: attempt_limit,
        _ERROR_KIND_KEY: error_kind(cast(LLMError, error)).value,
        _ERROR_TYPE_KEY: type(error).__name__,
        _PARTIAL_OUTPUT_ABANDONED_KEY: partial_output_emitted,
    }
    log_llm_call(
        level=RuntimeEventLevel.INFO,
        message=retry_message,
        data=retry_data,
    )
    emit_llm_runtime_event(
        pipe,
        kind=LifecycleKind.RETRYING,
        level=RuntimeEventLevel.INFO,
        message=retry_message,
        data=retry_data,
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
) -> _ChatCompletionResult:
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
    response_type = (
        _JSON_OBJECT_RESPONSE_TYPE
        if endpoint.output_format == _JSON_OUTPUT_FORMAT
        else _TEXT_RESPONSE_TYPE
    )
    request = ChaCompletionRequest(
        model=endpoint.model_name,
        messages=messages_scrubber(endpoint.message_mapper(messages)),
        temperature=endpoint.temperature,
        response_format=ResponseFormat(type=response_type),
    )
    if endpoint.extra_body:
        request[_EXTRA_BODY_FIELD] = endpoint.extra_body
    return request


def _call_non_streaming_llm_api(
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
) -> _ChatCompletionResult:
    response = endpoint.client.chat.completions.create(**request)  # type:ignore
    usage = getattr(response, _USAGE_FIELD, None)
    content = response.choices[0].message.content  # type:ignore
    return _ChatCompletionResult(
        content=content if isinstance(content, str) else "",
        telemetry=_telemetry_from_usage(endpoint=endpoint, usage=usage),
        diagnostics=LLMResponseDiagnostics.from_response(response, usage),
    )


def _stream_chunk_usage(chunk: object) -> object | None:
    """Return usage supplied by one provider stream chunk, if any."""
    if isinstance(chunk, dict):
        return chunk.get(_USAGE_FIELD)
    return getattr(chunk, _USAGE_FIELD, None)


def _call_streaming_llm_api(
    endpoint: LLMEndpoint,
    request: ChaCompletionRequest,
    on_delta: Callable[[str], None],
    control_signals: tuple[ControlSignal, ...],
    call_abandoned: Event | None = None,
) -> _ChatCompletionResult:
    stream_request = request | {
        _STREAM_KEY: True,
        _STREAM_OPTIONS_FIELD: {_INCLUDE_USAGE_FIELD: True},
    }
    try:
        stream = endpoint.client.chat.completions.create(**stream_request)  # type:ignore
    except TypeError:
        stream_request.pop(_STREAM_OPTIONS_FIELD, None)
        stream = endpoint.client.chat.completions.create(**stream_request)  # type:ignore

    chunks: list[str] = []
    usage: object | None = None
    diagnostics = LLMResponseDiagnostics.for_stream()
    try:
        for chunk in stream:
            if _call_abandoned(call_abandoned) or _any_signal_set(control_signals):
                break
            chunk_usage = _stream_chunk_usage(chunk)
            if chunk_usage is not None:
                usage = chunk_usage
            delta = _stream_delta_content(chunk)
            diagnostics.observe_chunk(chunk, has_content=bool(delta))
            if not delta:
                continue
            chunks.append(delta)
            on_delta(delta)
    finally:
        close = getattr(stream, _STREAM_CLOSE_METHOD, None)
        if callable(close):
            try:
                close()
            except Exception as error:  # noqa: BLE001 - cleanup must not mask outcome
                log_llm_call(
                    level=RuntimeEventLevel.WARNING,
                    message=(
                        "Failed to close LLM provider stream "
                        f"(error_type={type(error).__name__})"
                    ),
                    data={_ERROR_TYPE_KEY: type(error).__name__},
                )

    diagnostics.observe_usage(usage)
    return _ChatCompletionResult(
        content="".join(chunks),
        telemetry=_telemetry_from_usage(endpoint=endpoint, usage=usage),
        diagnostics=diagnostics,
    )


def _any_signal_set(control_signals: tuple[ControlSignal, ...]) -> bool:
    return any(signal.is_set for signal in control_signals)


def _call_abandoned(call_abandoned: Event | None) -> bool:
    return call_abandoned is not None and call_abandoned.is_set()


def _stream_delta_content(chunk) -> str:
    choices = getattr(chunk, _CHOICES_FIELD, None)
    if choices is None and isinstance(chunk, dict):
        choices = chunk.get(_CHOICES_FIELD)
    if not choices:
        return ""
    first_choice = choices[0]
    delta = getattr(first_choice, _DELTA_FIELD, None)
    if delta is None and isinstance(first_choice, dict):
        delta = first_choice.get(_DELTA_FIELD)
    content = getattr(delta, _CONTENT_FIELD, None)
    if content is None and isinstance(delta, dict):
        content = delta.get(_CONTENT_FIELD)
    if isinstance(content, str):
        return content
    return ""


def _telemetry_from_usage(*, endpoint: LLMEndpoint, usage) -> LLMTelemetryDict:
    if isinstance(usage, dict):
        token_in = usage.get(_PROMPT_TOKENS_FIELD)
        token_out = usage.get(_COMPLETION_TOKENS_FIELD)
    else:
        token_in = (
            getattr(usage, _PROMPT_TOKENS_FIELD, None)
            if usage is not None
            else None
        )
        token_out = (
            getattr(usage, _COMPLETION_TOKENS_FIELD, None)
            if usage is not None
            else None
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
        _CALL_ID_KEY: call_id,
        _ENDPOINT_KEY: endpoint.api_name,
        _MODEL_KEY: endpoint.model_name,
        _AUDIO_BYTES_KEY: len(audio),
    }
    log_llm_call(
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
        log_llm_call(
            level=RuntimeEventLevel.INFO,
            message=(
                f"Transcription call succeeded: {endpoint.api_name}/{endpoint.model_name}"
            ),
            data=event_data
            | {
                _DURATION_MS_KEY: round(
                    (time.monotonic() - started) * _MILLISECONDS_PER_SECOND
                ),
                _RESPONSE_CHARS_KEY: len(text),
            },
        )
        return text

    request: dict[str, object] = {
        _FILE_FIELD: (filename, audio, content_type),
        _MODEL_FIELD: endpoint.model_name,
        _TEMPERATURE_FIELD: (
            endpoint.temperature if temperature is None else temperature
        ),
    }
    resolved_language = endpoint.language if language is None else language
    if resolved_language is not None:
        request[_LANGUAGE_FIELD] = resolved_language
    resolved_prompt = endpoint.prompt if prompt is None else prompt
    if resolved_prompt is not None:
        request[_PROMPT_FIELD] = resolved_prompt

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
                    _ENDPOINT_KEY: endpoint.api_name,
                    _MODEL_KEY: endpoint.model_name,
                    _AUDIO_BYTES_KEY: len(audio),
                },
            )
            return getattr(response, _TEXT_FIELD).strip()
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
                    operation=_TRANSCRIPTION_OPERATION,
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
        log_llm_failure(endpoint, call_id, started, error)
        raise
    log_llm_call(
        level=RuntimeEventLevel.INFO,
        message=(
            f"Transcription call succeeded: {endpoint.api_name}/{endpoint.model_name}"
        ),
        data=event_data
        | {
            _DURATION_MS_KEY: round(
                (time.monotonic() - started) * _MILLISECONDS_PER_SECOND
            ),
            _RESPONSE_CHARS_KEY: len(text),
        },
    )
    return text
