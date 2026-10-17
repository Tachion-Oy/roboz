# ruff: noqa: F403, F405
"""Cancellable synchronous OpenAI-compatible chat and transcription calls."""

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from threading import Event
from typing import Final, Literal, NotRequired, TypedDict, cast
from uuid import uuid4

from roboz.exceptions import *  # noqa: F403
from roboz.models import Image, Message, Role
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
from roboz.llm.openai_compatible import OpenAIResponseFormat
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
    role_to_user_mapper,
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
_MILLISECONDS_PER_SECOND: Final[int] = 1_000
_IMAGE_NAME_MAX_CHARS: Final[int] = 256
_IMAGE_OMISSION_MAX_CHARS: Final[int] = 2_048
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
    if isinstance(error, LLMError):
        return error
    if isinstance(error, ExternalCallInterruptedError):
        return LLMCallInterruptedError("LLM call was interrupted")
    if isinstance(error, ExternalCallCancelledError):
        return LLMCallCancelledError("LLM call was cancelled")
    if isinstance(error, ExternalCallTimeoutError):
        return LLMCallTimeoutError("LLM call timed out")
    return classify_llm_provider_error(error, endpoint)


class ResponseFormat(TypedDict):
    """Provider request fragment selecting JSON or text output."""

    type: Literal["json_object", "text"]


class ChaCompletionRequest(TypedDict):
    """Normalized provider request for a chat completion."""

    model: str
    messages: list[dict]
    temperature: float
    response_format: OpenAIResponseFormat
    extra_body: NotRequired[RequestOptions]


class _TranscriptionRequest(TypedDict):
    file: tuple[str, bytes, str]
    model: str
    temperature: float
    language: NotRequired[str]
    prompt: NotRequired[str]


def _image_omission_note(images: list[dict]) -> str:
    """Bound synthesized references, including escaping and the remaining count."""
    prefix = "\n[Images omitted because this model cannot read images: "
    suffix = ". Use the conversation text; do not infer image contents.]"
    # Reserve room for a remainder count even when the next name does not fit.
    remaining_chars = (
        _IMAGE_OMISSION_MAX_CHARS - len(prefix) - len(suffix)
        - len(f", and {len(images)} more images")
    )
    references: list[str] = []
    for index, image in enumerate(images, 1):
        name = Image.model_validate(image).filename or f"image-{index}"
        if len(name) > _IMAGE_NAME_MAX_CHARS:
            name = name[:_IMAGE_NAME_MAX_CHARS - 1] + "…"
        reference = json.dumps(name, ensure_ascii=False)
        size = len(reference) + (2 if references else 0)
        if size > remaining_chars:
            break
        references.append(reference)
        remaining_chars -= size
    if remaining := len(images) - len(references):
        references.append(f"and {remaining} more images")
    return prefix + ", ".join(references) + suffix


def _encode_image_messages(
    records: list[dict], *, accepts_images: bool
) -> tuple[list[dict], bool]:
    """Encode scrubbed attachments for the destination without changing records."""
    messages = []
    omitted = False
    for record in records:
        payload = record.copy()
        images = payload.pop("images", [])
        if images and not isinstance(payload.get("content"), str):
            raise LLMImageInputError(
                "Message text must be a string; use an empty string for image-only messages."
            )
        if images and not accepts_images:
            payload["content"] += _image_omission_note(images)
            images = []
            omitted = True
        if images:
            if payload.get("role") != Role.USER:
                raise LLMImageInputError("Image inputs require a user message")
            payload["content"] = [
                {"type": "text", "text": payload["content"]},
                *(
                    {
                        "type": "image_url",
                        "image_url": Image.model_validate(image).model_dump(
                            include={"url", "detail"}, exclude_none=True
                        ),
                    }
                    for image in images
                ),
            ]
        messages.append(payload)
    return messages, omitted


class _LLMCallRunner:
    """Own the state and lifecycle of one synchronous chat call."""

    def __init__(
        self,
        endpoint: EndpointLike,
        messages: list[Message],
        *,
        messages_scrubber: Callable[[list[Message]], list[dict]],
        on_delta: Callable[[str], None] | None,
        pipe: EventPipe | None,
        timeout_s: float | None,
        max_attempts: int,
        retry_base_delay_s: float,
        retry_max_delay_s: float,
        start_replacement_stream: Callable[[], None] | None,
    ) -> None:
        self._endpoint = resolve_endpoint(endpoint)
        self._signals = pipe.control_signals if pipe is not None else (ControlSignal(),)
        self._call_id = str(uuid4())
        self._started = time.monotonic()
        self._messages = messages
        self._messages_scrubber = messages_scrubber
        self._on_delta = on_delta
        self._pipe = pipe
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._retry_base_delay_s = retry_base_delay_s
        self._retry_max_delay_s = retry_max_delay_s
        self._start_replacement_stream = start_replacement_stream
        self._request: ChaCompletionRequest | None = None
        self._label = f"llm:{self._endpoint.api_name}/{self._endpoint.model_name}"
        self._event_data = llm_event_data(
            self._endpoint, call_id=self._call_id, timeout_s=timeout_s
        )

    def run(self) -> tuple[str, LLMTelemetryDict]:
        self._report_started()
        try:
            if isinstance(self._endpoint, MockLLMEndpoint):
                result = self._call_mock_llm_api()
            else:
                self._request = self._chat_completion_request()
                result = run_with_retry(
                    self._run_attempt,
                    retryable_errors=_RETRYABLE_LLM_ERRORS,
                    control_signals=self._signals,
                    max_attempts=self._max_attempts,
                    base_delay_s=self._retry_base_delay_s,
                    max_delay_s=self._retry_max_delay_s,
                    label=self._label,
                    prepare_retry=self._prepare_retry,
                )
            for signal in self._signals:
                signal.raise_if_set()
        except Exception as exc:
            error = _classify_call_error(exc, self._endpoint)
            log_llm_failure(self._endpoint, self._call_id, self._started, error)
            emit_llm_failure_event(
                self._pipe, self._endpoint, self._call_id, self._started, error
            )
            if error is exc:
                raise
            raise error from exc
        self._report_succeeded(result)
        return result.content, result.telemetry

    def _report_started(self) -> None:
        started_message = (
            f"LLM call started: {self._endpoint.api_name}/{self._endpoint.model_name}"
        )
        started_data = self._event_data | {
            _MESSAGE_COUNT_KEY: len(self._messages),
            _MESSAGE_CHARS_KEY: sum(len(message.content) for message in self._messages),
            _OUTPUT_FORMAT_KEY: getattr(
                self._endpoint, _OUTPUT_FORMAT_KEY, _MOCK_OUTPUT_FORMAT
            ),
            _STREAM_KEY: getattr(self._endpoint, _STREAM_KEY, False),
        }
        log_llm_call(
            level=RuntimeEventLevel.INFO,
            message=started_message,
            data=started_data,
        )
        emit_llm_runtime_event(
            self._pipe,
            kind=LifecycleKind.STARTED,
            level=RuntimeEventLevel.INFO,
            message=started_message,
            data=started_data,
        )

    def _report_succeeded(self, result: _ChatCompletionResult) -> None:
        duration_ms = round((time.monotonic() - self._started) * 1000)
        succeeded_message = (
            f"LLM call succeeded: {self._endpoint.api_name}/{self._endpoint.model_name}"
        )
        succeeded_data = (
            self._event_data
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
            self._pipe,
            kind=LifecycleKind.SUCCEEDED,
            level=RuntimeEventLevel.INFO,
            message=succeeded_message,
            data=succeeded_data,
        )

    def _chat_completion_request(self) -> ChaCompletionRequest:
        assert isinstance(self._endpoint, LLMEndpoint)
        response_format: OpenAIResponseFormat = (
            {"type": "json_object"}
            if self._endpoint.output_format == _JSON_OUTPUT_FORMAT
            else {"type": "text"}
        )
        request = ChaCompletionRequest(
            model=self._endpoint.model_name,
            messages=self._chat_completion_messages(),
            temperature=self._endpoint.temperature,
            response_format=response_format,
        )
        if self._endpoint.extra_body:
            request[_EXTRA_BODY_FIELD] = self._endpoint.extra_body
        return request

    def _chat_completion_messages(self) -> list[dict]:
        """Encode each scrubbed record using only its own text and attachments."""
        mapper = (
            role_to_user_mapper
            if isinstance(self._endpoint, MockLLMEndpoint)
            else self._endpoint.message_mapper
        )
        # Custom mappers/scrubbers receive a detached outgoing representation.
        outgoing = [
            message.model_copy(deep=True) for message in self._messages
        ]
        records = self._messages_scrubber(mapper(outgoing))
        messages, omitted = _encode_image_messages(
            records, accepts_images=self._endpoint.accepts_images
        )
        if self._pipe is not None:
            self._pipe.report_image_omission(
                endpoint=self._endpoint.api_name,
                model=self._endpoint.model_name,
                omitted=omitted,
            )
        return messages

    def _call_mock_llm_api(self) -> _ChatCompletionResult:
        assert isinstance(self._endpoint, MockLLMEndpoint)
        self._raise_for_mock_control_signal()
        self._chat_completion_messages()
        if not self._endpoint.mock_responses:
            raise RuntimeError("Mock script exhausted before stop")
        mock_response = self._endpoint.mock_responses.pop(0)
        self._raise_for_mock_control_signal()
        if isinstance(mock_response, Exception):
            raise mock_response
        if self._on_delta is not None:
            on_delta = self._delta_gate()
            for delta in self._iter_text_chunks(mock_response):
                on_delta(delta)
        return _ChatCompletionResult(
            content=mock_response,
            telemetry=cast(LLMTelemetryDict, {}),
            diagnostics=LLMResponseDiagnostics(),
        )

    def _raise_for_mock_control_signal(self) -> None:
        for signal in self._signals:
            signal.raise_if_set()

    def _run_attempt(self, retry_state: RetryState) -> _ChatCompletionResult:
        assert isinstance(self._endpoint, LLMEndpoint)
        assert self._request is not None
        attempt_abandoned = Event()
        call = partial(self._call_chat_completion, attempt_abandoned, retry_state)
        try:
            return run_cancellable_external_call(
                call,
                control_signals=self._signals,
                timeout_s=self._timeout_s,
                label=self._label,
                call_id=self._call_id,
                attempt=retry_state.attempt,
                log_data={
                    _ENDPOINT_KEY: self._endpoint.api_name,
                    _MODEL_KEY: self._endpoint.model_name,
                    _MESSAGE_COUNT_KEY: len(self._request[_MESSAGES_FIELD]),
                    _STREAM_KEY: bool(self._on_delta and self._endpoint.stream),
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
                    self._endpoint,
                    operation=_CHAT_OPERATION,
                    call_id=self._call_id,
                    attempt=retry_state.attempt,
                    error=exc,
                )
            error = _classify_call_error(exc, self._endpoint)
            if error is exc:
                raise
            raise error from exc
        except BaseException:
            attempt_abandoned.set()
            raise

    def _prepare_retry(
        self,
        error: Exception,
        failed_attempt: int,
        attempt_limit: int,
        partial_output_emitted: bool,
    ) -> bool:
        if partial_output_emitted and self._start_replacement_stream is None:
            return False

        retry_message = (
            f"LLM call retrying: {self._endpoint.api_name}/{self._endpoint.model_name} "
            f"(attempt {failed_attempt + 1}/{attempt_limit})"
        )
        retry_data = self._event_data | {
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
            self._pipe,
            kind=LifecycleKind.RETRYING,
            level=RuntimeEventLevel.INFO,
            message=retry_message,
            data=retry_data,
        )
        if partial_output_emitted:
            assert self._start_replacement_stream is not None
            self._start_replacement_stream()
        return True

    def _call_chat_completion(
        self,
        call_abandoned: Event | None = None,
        retry_state: RetryState | None = None,
    ) -> _ChatCompletionResult:
        assert isinstance(self._endpoint, LLMEndpoint)
        for signal in self._signals:
            signal.raise_if_set()
        if self._on_delta is not None and self._endpoint.stream:
            return self._call_streaming_llm_api(
                self._delta_gate(call_abandoned, retry_state), call_abandoned
            )
        return self._call_non_streaming_llm_api()

    def _delta_gate(
        self,
        call_abandoned: Event | None = None,
        retry_state: RetryState | None = None,
    ) -> Callable[[str], None]:
        on_delta = self._on_delta
        assert on_delta is not None

        def gate(delta: str) -> None:
            if self._call_abandoned(call_abandoned) or self._any_signal_set():
                return
            if retry_state is not None:
                retry_state.mark_output_emitted()
            on_delta(delta)

        return gate

    def _call_non_streaming_llm_api(self) -> _ChatCompletionResult:
        assert isinstance(self._endpoint, LLMEndpoint)
        assert self._request is not None
        response = self._endpoint.client.chat.completions.create(**self._request)
        usage = getattr(response, _USAGE_FIELD, None)
        content = response.choices[0].message.content
        return _ChatCompletionResult(
            content=content if isinstance(content, str) else "",
            telemetry=self._telemetry_from_usage(usage),
            diagnostics=LLMResponseDiagnostics.from_response(response, usage),
        )

    def _call_streaming_llm_api(
        self,
        on_delta: Callable[[str], None],
        call_abandoned: Event | None = None,
    ) -> _ChatCompletionResult:
        assert isinstance(self._endpoint, LLMEndpoint)
        assert self._request is not None
        try:
            stream = self._endpoint.client.chat.completions.create(
                **self._request, stream=True, stream_options={"include_usage": True}
            )
        except TypeError:
            stream = self._endpoint.client.chat.completions.create(
                **self._request, stream=True
            )

        chunks: list[str] = []
        usage: object | None = None
        diagnostics = LLMResponseDiagnostics.for_stream()
        try:
            for chunk in stream:
                if self._call_abandoned(call_abandoned) or self._any_signal_set():
                    break
                chunk_usage = self._stream_chunk_usage(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage
                delta = self._stream_delta_content(chunk)
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
            telemetry=self._telemetry_from_usage(usage),
            diagnostics=diagnostics,
        )

    def _any_signal_set(self) -> bool:
        return any(signal.is_set for signal in self._signals)

    @staticmethod
    def _call_abandoned(call_abandoned: Event | None) -> bool:
        return call_abandoned is not None and call_abandoned.is_set()

    @staticmethod
    def _stream_chunk_usage(chunk: object) -> object | None:
        """Return usage supplied by one provider stream chunk, if any."""
        if isinstance(chunk, dict):
            return chunk.get(_USAGE_FIELD)
        return getattr(chunk, _USAGE_FIELD, None)

    @staticmethod
    def _stream_delta_content(chunk: object) -> str:
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

    def _telemetry_from_usage(self, usage: object) -> LLMTelemetryDict:
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
                endpoint=self._endpoint.api_name,
                model=self._endpoint.model_name,
                token_input=token_in,
                token_output=token_out,
            ).model_dump(mode="json", exclude_none=True),
        )

    @staticmethod
    def _iter_text_chunks(text: str, *, chunk_size: int = 24) -> Iterator[str]:
        for start in range(0, len(text), chunk_size):
            yield text[start : start + chunk_size]


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

    Custom ``messages_scrubber`` callbacks own their output records, including
    the optional generic ``images`` field. Retain that field to send attachments;
    omit it to remove them. Records may be filtered or reordered. Image content
    parts are constructed from each returned record only, after scrubbing.
    The mapper and scrubber receive detached messages. A destination without
    image support receives text and filename references instead of attachments;
    original history remains available for later requests to a vision model.
    When supplied, ``pipe`` reports changes in image omission as information.
    """
    return _LLMCallRunner(
        endpoint,
        messages,
        messages_scrubber=messages_scrubber,
        on_delta=on_delta,
        pipe=pipe,
        timeout_s=timeout_s,
        max_attempts=max_attempts,
        retry_base_delay_s=retry_base_delay_s,
        retry_max_delay_s=retry_max_delay_s,
        start_replacement_stream=start_replacement_stream,
    ).run()


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

    request: _TranscriptionRequest = {
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
                    operation=_TRANSCRIPTION_OPERATION,
                    call_id=call_id,
                    attempt=retry_state.attempt,
                    error=exc,
                )
            error = _classify_call_error(exc, endpoint)
            if error is exc:
                raise
            raise error from exc

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
