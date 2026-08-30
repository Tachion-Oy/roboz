# ruff: noqa: F403, F405
import logging
import time
from enum import StrEnum, auto

from roboz.exceptions import *  # noqa: F403
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

type LLMEventData = dict[str, str | int | float | bool | None]


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
    """Emit an LLM lifecycle event containing deliberate metadata only.

    No sanitization is attempted here. Callers must not pass prompts, responses,
    URLs, headers, exception messages, or other provider-controlled text.
    """
    logger.log(
        level.logging_level,
        "%s (kind=%s, data=%s)",
        message,
        kind,
        data,
    )
    if pipe is None:
        return
    pipe.emit_runtime_event(
        category=RuntimeEventCategory.LLM,
        kind=kind,
        level=level,
        message=message,
        data=data,
    )


def emit_llm_failure_event(
    pipe: EventPipe | None,
    endpoint: (
        LLMEndpoint
        | MockLLMEndpoint
        | TranscriptionEndpoint
        | MockTranscriptionEndpoint
    ),
    call_id: str,
    started: float,
    error: LLMError,
) -> None:
    duration_ms = round((time.monotonic() - started) * 1000)
    failure = observed_llm_failure(error)
    emit_llm_runtime_event(
        pipe,
        kind=failure.kind,
        level=failure.level,
        message=(f"LLM call {failure.kind}: {endpoint.api_name}/{endpoint.model_name}"),
        data=llm_event_data(endpoint, call_id=call_id)
        | {
            "duration_ms": duration_ms,
            "error_kind": error_kind(error).value,
            "error_type": type(error).__name__,
        },
    )


def log_llm_provider_error(
    endpoint: (
        LLMEndpoint
        | MockLLMEndpoint
        | TranscriptionEndpoint
        | MockTranscriptionEndpoint
    ),
    *,
    operation: str,
    call_id: str,
    attempt: int,
    error: Exception,
) -> None:
    """Log raw provider diagnostics without putting them on the runtime event pipe.

    Provider responses are operationally useful but may contain sensitive text. This
    function is therefore intentionally server-log-only: callers must never copy its
    raw fields into persisted/runtime events.
    """
    response = _safe_attr(error, "response")
    status_code = _safe_attr(error, "status_code", "status")
    if status_code is None:
        status_code = _safe_attr(response, "status_code", "status")

    request_id = _safe_attr(error, "request_id")
    if request_id is None:
        request_id = _response_request_id(response)

    provider_body = _safe_attr(error, "body")
    if provider_body is None:
        provider_body = _safe_attr(response, "text")

    logger.error(
        "Raw LLM provider error (operation=%s, endpoint=%s, model=%s, "
        "call_id=%s, attempt=%d, provider_error_type=%s, status_code=%s, "
        "request_id=%s, provider_message=%s, provider_body=%r)",
        operation,
        endpoint.api_name,
        endpoint.model_name,
        call_id,
        attempt,
        type(error).__name__,
        status_code,
        request_id,
        str(error),
        provider_body,
        exc_info=(type(error), error, error.__traceback__),
    )


def llm_event_data(
    endpoint: (
        LLMEndpoint
        | MockLLMEndpoint
        | TranscriptionEndpoint
        | MockTranscriptionEndpoint
    ),
    *,
    call_id: str,
    timeout_s: float | None = None,
) -> LLMEventData:
    data: LLMEventData = {
        "call_id": call_id,
        "endpoint": endpoint.api_name,
        "model": endpoint.model_name,
    }
    if timeout_s is not None:
        data["timeout_s"] = timeout_s
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
    endpoint: (
        LLMEndpoint
        | MockLLMEndpoint
        | TranscriptionEndpoint
        | MockTranscriptionEndpoint
    ),
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
_TRANSPORT_ERROR_MODULES = ("httpx", "httpcore")
_TRANSPORT_ERROR_NAMES = ("TransportError", "NetworkError", "TimeoutException")
_TRANSIENT_OS_ERRNOS = frozenset(
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
    headers = _safe_attr(response, "headers")
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return None
    for name in ("x-request-id", "request-id", "x-correlation-id"):
        try:
            value = get_header(name)
        except Exception:  # noqa: BLE001 - malformed headers are non-essential
            return None
        if value is not None:
            return value
    return None
