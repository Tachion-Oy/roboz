# ruff: noqa: F403, F405
import logging
import time
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final, Self

from roboz.exceptions import *  # noqa: F403
from roboz.runtime._logging import LogScalar, log_with_data
from roboz.runtime.observability import (
    ObservedFailure,
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.runtime.pipe import EventPipe
from roboz.llm.endpoints import (
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
)

logger = logging.getLogger(__name__)

type LLMEventData = dict[str, LogScalar]
type _AnyEndpoint = (
    LLMEndpoint
    | MockLLMEndpoint
    | TranscriptionEndpoint
    | MockTranscriptionEndpoint
)

_CHOICES_FIELD: Final[str] = "choices"
_MESSAGE_FIELD: Final[str] = "message"
_DELTA_FIELD: Final[str] = "delta"
_FINISH_REASON_FIELD: Final[str] = "finish_reason"
_GENERATION_ID_FIELD: Final[str] = "id"
_COMPLETION_TOKEN_DETAILS_FIELD: Final[str] = "completion_tokens_details"
_REASONING_TOKENS_FIELD: Final[str] = "reasoning_tokens"
_REASONING_DIRECT_FIELDS: Final[tuple[str, ...]] = (
    "reasoning",
    "reasoning_content",
)
_REASONING_DETAILS_FIELD: Final[str] = "reasoning_details"
_REASONING_DETAIL_TEXT_FIELDS: Final[tuple[str, ...]] = ("text", "summary")

_REASONING_CHARS_KEY: Final[str] = "reasoning_chars"
_REASONING_CHUNKS_KEY: Final[str] = "reasoning_chunks"
_REASONING_TOKENS_KEY: Final[str] = "reasoning_tokens"
_FINISH_REASON_KEY: Final[str] = "finish_reason"
_PROVIDER_GENERATION_ID_KEY: Final[str] = "provider_generation_id"
_STREAM_CHUNKS_KEY: Final[str] = "stream_chunks"
_CONTENT_CHUNKS_KEY: Final[str] = "content_chunks"
_CALL_ID_KEY: Final[str] = "call_id"
_ENDPOINT_KEY: Final[str] = "endpoint"
_MODEL_KEY: Final[str] = "model"
_TIMEOUT_SECONDS_KEY: Final[str] = "timeout_s"
_DURATION_MS_KEY: Final[str] = "duration_ms"
_ERROR_KIND_KEY: Final[str] = "error_kind"
_ERROR_TYPE_KEY: Final[str] = "error_type"
_OPERATION_KEY: Final[str] = "operation"
_ATTEMPT_KEY: Final[str] = "attempt"
_PROVIDER_ERROR_TYPE_KEY: Final[str] = "provider_error_type"
_STATUS_CODE_KEY: Final[str] = "status_code"
_REQUEST_ID_KEY: Final[str] = "request_id"
_MILLISECONDS_PER_SECOND: Final[int] = 1_000

_RESPONSE_ATTRIBUTE: Final[str] = "response"
_STATUS_ATTRIBUTES: Final[tuple[str, ...]] = ("status_code", "status")
_REQUEST_ID_ATTRIBUTE: Final[str] = "request_id"
_HEADERS_ATTRIBUTE: Final[str] = "headers"
_REQUEST_ID_HEADERS: Final[tuple[str, ...]] = (
    "x-request-id",
    "request-id",
    "x-correlation-id",
)


@dataclass
class LLMResponseDiagnostics:
    """Safe provider-response metadata that never retains response text."""

    reasoning_chars: int = 0
    reasoning_chunks: int = 0
    reasoning_tokens: int | None = None
    finish_reason: str | None = None
    provider_generation_id: str | None = None
    stream_chunks: int | None = None
    content_chunks: int | None = None

    @classmethod
    def from_response(cls, response: object, usage: object) -> Self:
        diagnostics = cls()
        choice = cls._first_choice(response)
        diagnostics._observe_reasoning(cls._field(choice, _MESSAGE_FIELD))
        diagnostics.observe_usage(usage)

        finish_reason = cls._field(choice, _FINISH_REASON_FIELD)
        if isinstance(finish_reason, str) and finish_reason:
            diagnostics.finish_reason = finish_reason

        generation_id = cls._field(response, _GENERATION_ID_FIELD)
        if isinstance(generation_id, str) and generation_id:
            diagnostics.provider_generation_id = generation_id
        return diagnostics

    @classmethod
    def for_stream(cls) -> Self:
        return cls(stream_chunks=0, content_chunks=0)

    def observe_chunk(self, chunk: object, *, has_content: bool) -> None:
        assert self.stream_chunks is not None
        assert self.content_chunks is not None
        self.stream_chunks += 1
        self.content_chunks += int(has_content)

        if self.provider_generation_id is None:
            generation_id = self._field(chunk, _GENERATION_ID_FIELD)
            if isinstance(generation_id, str) and generation_id:
                self.provider_generation_id = generation_id

        choice = self._first_choice(chunk)
        finish_reason = self._field(choice, _FINISH_REASON_FIELD)
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason

        self._observe_reasoning(self._field(choice, _DELTA_FIELD))

    def observe_usage(self, usage: object) -> None:
        details = self._field(usage, _COMPLETION_TOKEN_DETAILS_FIELD)
        reasoning_tokens = self._field(details, _REASONING_TOKENS_FIELD)
        if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool):
            self.reasoning_tokens = reasoning_tokens

    def event_data(self) -> LLMEventData:
        return {
            _REASONING_CHARS_KEY: self.reasoning_chars,
            _REASONING_CHUNKS_KEY: self.reasoning_chunks,
            _REASONING_TOKENS_KEY: self.reasoning_tokens,
            _FINISH_REASON_KEY: self.finish_reason,
            _PROVIDER_GENERATION_ID_KEY: self.provider_generation_id,
            _STREAM_CHUNKS_KEY: self.stream_chunks,
            _CONTENT_CHUNKS_KEY: self.content_chunks,
        }

    def _observe_reasoning(self, value: object) -> None:
        for field_name in _REASONING_DIRECT_FIELDS:
            direct = self._field(value, field_name)
            if isinstance(direct, str) and direct:
                self.reasoning_chars += len(direct)
                self.reasoning_chunks += 1
                return

        details = self._field(value, _REASONING_DETAILS_FIELD)
        if not isinstance(details, (list, tuple)) or not details:
            return

        self.reasoning_chunks += 1
        for detail in details:
            for field_name in _REASONING_DETAIL_TEXT_FIELDS:
                part = self._field(detail, field_name)
                if isinstance(part, str) and part:
                    self.reasoning_chars += len(part)
                    break

    @staticmethod
    def _field(value: object, name: str) -> object:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @classmethod
    def _first_choice(cls, response: object) -> object | None:
        choices = cls._field(response, _CHOICES_FIELD)
        if not isinstance(choices, (list, tuple)) or not choices:
            return None
        return choices[0]


class LLMErrorKind(StrEnum):
    INTERRUPTED = auto()
    CANCELLED = auto()
    TIMEOUT = auto()
    AUTH = auto()
    INSUFFICIENT_FUNDS = auto()
    RATE_LIMIT = auto()
    CONTEXT_LIMIT = auto()
    PROVIDER_REQUEST = auto()
    PROVIDER_UNAVAILABLE = auto()
    UNKNOWN_PROVIDER_ERROR = auto()


def emit_llm_runtime_event(
    pipe: EventPipe | None,
    *,
    kind: RuntimeEventKind,
    level: RuntimeEventLevel,
    message: str,
    data: LLMEventData,
) -> None:
    """Emit an LLM lifecycle event through the supplied event pipe.

    No sanitization is attempted here. Callers must not pass prompts, responses,
    URLs, headers, exception messages, or other provider-controlled text.
    """
    if pipe is None:
        return
    pipe.emit_runtime_event(
        category=RuntimeEventCategory.LLM,
        kind=kind,
        level=level,
        message=message,
        data=data,
    )


def log_llm_call(
    *,
    level: RuntimeEventLevel,
    message: str,
    data: LLMEventData,
) -> None:
    """Write an LLM operational diagnostic."""
    log_with_data(logger, level.logging_level, message, data)


def emit_llm_failure_event(
    pipe: EventPipe | None,
    endpoint: _AnyEndpoint,
    call_id: str,
    started: float,
    error: LLMError,
) -> None:
    duration_ms = round(
        (time.monotonic() - started) * _MILLISECONDS_PER_SECOND
    )
    failure = observed_llm_failure(error)
    emit_llm_runtime_event(
        pipe,
        kind=failure.kind,
        level=failure.level,
        message=(f"LLM call {failure.kind}: {endpoint.api_name}/{endpoint.model_name}"),
        data=llm_event_data(endpoint, call_id=call_id)
        | {
            _DURATION_MS_KEY: duration_ms,
            _ERROR_KIND_KEY: error_kind(error).value,
            _ERROR_TYPE_KEY: type(error).__name__,
        },
    )


def log_llm_failure(
    endpoint: _AnyEndpoint,
    call_id: str,
    started: float,
    error: LLMError,
) -> None:
    duration_ms = round((time.monotonic() - started) * 1000)
    failure = observed_llm_failure(error)
    log_llm_call(
        level=failure.level,
        message=f"LLM call {failure.kind}: {endpoint.api_name}/{endpoint.model_name}",
        data=llm_event_data(endpoint, call_id=call_id)
        | {
            _DURATION_MS_KEY: duration_ms,
            _ERROR_KIND_KEY: error_kind(error).value,
            _ERROR_TYPE_KEY: type(error).__name__,
        },
    )


def log_llm_provider_error(
    endpoint: _AnyEndpoint,
    *,
    operation: str,
    call_id: str,
    attempt: int,
    error: Exception,
) -> None:
    """Log provider failure metadata without provider-controlled text."""
    response = _safe_attr(error, _RESPONSE_ATTRIBUTE)
    status_code = _safe_attr(error, *_STATUS_ATTRIBUTES)
    if status_code is None:
        status_code = _safe_attr(response, *_STATUS_ATTRIBUTES)

    request_id = _safe_attr(error, _REQUEST_ID_ATTRIBUTE)
    if request_id is None:
        request_id = _response_request_id(response)

    safe_status = _metadata_scalar(status_code)
    safe_request_id = _metadata_scalar(request_id)
    log_with_data(
        logger,
        logging.DEBUG,
        (
            f"LLM provider attempt failed: {endpoint.api_name}/{endpoint.model_name} "
            f"(attempt={attempt}, error_type={type(error).__name__}, "
            f"status={safe_status}, request_id={safe_request_id})"
        ),
        {
            _OPERATION_KEY: operation,
            _ENDPOINT_KEY: endpoint.api_name,
            _MODEL_KEY: endpoint.model_name,
            _CALL_ID_KEY: call_id,
            _ATTEMPT_KEY: attempt,
            _PROVIDER_ERROR_TYPE_KEY: type(error).__name__,
            _STATUS_CODE_KEY: safe_status,
            _REQUEST_ID_KEY: safe_request_id,
        },
    )


def _metadata_scalar(value: object) -> LogScalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return type(value).__name__


def llm_event_data(
    endpoint: _AnyEndpoint,
    *,
    call_id: str,
    timeout_s: float | None = None,
) -> LLMEventData:
    data: LLMEventData = {
        _CALL_ID_KEY: call_id,
        _ENDPOINT_KEY: endpoint.api_name,
        _MODEL_KEY: endpoint.model_name,
    }
    if timeout_s is not None:
        data[_TIMEOUT_SECONDS_KEY] = timeout_s
    return data


def observed_llm_failure(error: LLMError) -> ObservedFailure:
    level = (
        RuntimeEventLevel.WARNING
        if isinstance(error, LLMProviderRequestError)
        else RuntimeEventLevel.ERROR
    )
    match error:
        case LLMCallInterruptedError():
            return ObservedFailure.interrupted(level=level)
        case LLMCallCancelledError():
            return ObservedFailure.cancelled(level=level)
        case LLMCallTimeoutError():
            return ObservedFailure.timed_out(level=level)
        case _:
            return ObservedFailure.failed(level=level)


def error_kind(error: LLMError) -> LLMErrorKind:
    match error:
        case LLMCallInterruptedError():
            return LLMErrorKind.INTERRUPTED
        case LLMCallCancelledError():
            return LLMErrorKind.CANCELLED
        case LLMCallTimeoutError():
            return LLMErrorKind.TIMEOUT
        case LLMAuthError():
            return LLMErrorKind.AUTH
        case LLMInsufficientFundsError():
            return LLMErrorKind.INSUFFICIENT_FUNDS
        case LLMRateLimitExceededError():
            return LLMErrorKind.RATE_LIMIT
        case LLMContextLimitExceededError():
            return LLMErrorKind.CONTEXT_LIMIT
        case LLMProviderRequestError():
            return LLMErrorKind.PROVIDER_REQUEST
        case LLMProviderUnavailableError():
            return LLMErrorKind.PROVIDER_UNAVAILABLE
        case _:
            return LLMErrorKind.UNKNOWN_PROVIDER_ERROR


def classify_llm_provider_error(
    error: Exception,
    endpoint: _AnyEndpoint,
) -> LLMError:
    if isinstance(error, LLMError):
        return error
    rate_limit_error = getattr(endpoint, "rate_limit_error", Exception)
    context_length_error = getattr(endpoint, "context_length_error", Exception)
    if _matches_endpoint_error(error, rate_limit_error):
        return LLMRateLimitExceededError(str(error))
    if _matches_endpoint_error(error, context_length_error):
        return LLMContextLimitExceededError(str(error))

    status_code = _error_attr(error, "status_code", "status")
    provider_code = str(_error_attr(error, "code", "type", default="")).lower()
    text = str(error).lower()
    haystack = " ".join(part for part in [provider_code, text] if part)

    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 500 <= status_code < 600
    ):
        return LLMProviderUnavailableError(str(error))
    if status_code in {401, 403} or _has_any(
        haystack, "auth", "api key", "unauthorized", "forbidden"
    ):
        return LLMAuthError(str(error))
    if status_code == 402 or _has_any(
        haystack, "insufficient", "funds", "balance", "quota_exceeded"
    ):
        return LLMInsufficientFundsError(str(error))
    if status_code == 429 or ("rate" in haystack and "limit" in haystack):
        return LLMRateLimitExceededError(str(error))
    if _has_any(
        haystack, "context", "maximum context", "token limit", "too many tokens"
    ):
        return LLMContextLimitExceededError(str(error))
    if _has_any(
        haystack, "unavailable", "overloaded", "timeout"
    ) or _is_transport_error(error):
        return LLMProviderUnavailableError(str(error))
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 400 <= status_code < 500
    ):
        return LLMProviderRequestError(str(error))
    return LLMUnknownProviderError(str(error))


def _has_any(haystack: str, *needles: str) -> bool:
    return any(needle in haystack for needle in needles)


# Low-level socket/HTTP-transport failure signatures. A dropped or reset
# connection mid-stream is transient infrastructure, not a semantic failure, so
# it maps to the retryable LLMProviderUnavailableError. Detected structurally
# (exception module + OS errno) rather than by message so roboz core stays
# provider-agnostic and never imports httpx/httpcore.
_TRANSPORT_ERROR_MODULES: Final[tuple[str, ...]] = ("httpx", "httpcore")
_TRANSPORT_ERROR_NAMES: Final[tuple[str, ...]] = (
    "TransportError",
    "NetworkError",
    "TimeoutException",
)
_TRANSIENT_OS_ERRNOS: Final[frozenset[int]] = frozenset(
    {
        32,  # EPIPE (broken pipe)
        54,  # ECONNRESET (BSD/macOS)
        60,  # ETIMEDOUT (BSD/macOS)
        104,  # ECONNRESET (Linux)
        110,  # ETIMEDOUT (Linux)
        111,  # ECONNREFUSED (Linux)
    }
)


def _is_transport_error(error: Exception) -> bool:
    for exc in _cause_chain(error):
        if isinstance(exc, OSError) and exc.errno in _TRANSIENT_OS_ERRNOS:
            return True
        for klass in type(exc).__mro__:
            if (
                klass.__module__.split(".")[0] in _TRANSPORT_ERROR_MODULES
                and klass.__name__ in _TRANSPORT_ERROR_NAMES
            ):
                return True
    return False


def _cause_chain(error: BaseException):
    """Yield ``error`` and each linked ``__cause__``/``__context__``, so an
    OSError wrapped inside an httpx error is still inspected."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _matches_endpoint_error(error: Exception, error_type: type[Exception]) -> bool:
    return error_type is not Exception and isinstance(error, error_type)


def _error_attr(error: Exception, *names: str, default: object = None) -> object:
    for name in names:
        value = getattr(error, name, None)
        if value is not None:
            return value
    return default


def _safe_attr(value: object, *names: str) -> object:
    if value is None:
        return None
    for name in names:
        try:
            candidate = getattr(value, name, None)
        except Exception:  # noqa: BLE001 - diagnostics must not mask provider errors
            candidate = None
        if candidate is not None:
            return candidate
    return None


def _response_request_id(response: object) -> object:
    headers = _safe_attr(response, _HEADERS_ATTRIBUTE)
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return None
    for name in _REQUEST_ID_HEADERS:
        try:
            value = get_header(name)
        except Exception:  # noqa: BLE001 - malformed headers are non-essential
            return None
        if value is not None:
            return value
    return None
