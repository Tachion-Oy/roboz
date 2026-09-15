"""Raw text completions and structured parsing, validation, and retry prompting."""

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from json import JSONDecodeError
from typing import Any, Final, cast, overload

from pydantic import BaseModel, ValidationError

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMOutputFormatError,
    NonexistentTool,
)
from roboz.llm._truncation import get_truncated_messages_for_context
from roboz.llm.binding import LLMTelemetryDict
from roboz.llm.calls import call_llm_api as _call_llm_api
from roboz.llm.endpoints import EndpointLike
from roboz.llm.prompts import (
    ACTION_FIX_PROMPT,
    JSON_FIX_PROMPT,
    PYDANTIC_FIX_PROMPT,
)
from roboz.models import BaseNames, Invoke, Message, Role
from roboz.models._serialization import reduce_escapes
from roboz.models.truncation import ERROR_RETRY
from roboz.runtime._logging import LogScalar, log_with_data
from roboz.runtime.pipe import EventPipe
from roboz.tooling.core import Tool

logger = logging.getLogger(__name__)


type JSONValue = None | bool | int | float | str | list[JSONValue] | JSONDict
type JSONDict = dict[str, JSONValue]
type AnyTool = Tool[Any, Any]
type _CompletionCaller = Callable[[list[Message]], tuple[str, LLMTelemetryDict]]


_THINK_TAGS: Final[tuple[str, ...]] = ("</think>", "◁/think▷")

_ATTEMPT_KEY: Final[str] = "attempt"
_MAX_ATTEMPTS_KEY: Final[str] = "max_attempts"
_ERROR_TYPE_KEY: Final[str] = "error_type"
_RESPONSE_CHARS_KEY: Final[str] = "response_chars"
_LINE_KEY: Final[str] = "line"
_COLUMN_KEY: Final[str] = "column"
_POSITION_KEY: Final[str] = "position"
_VALIDATION_ERRORS_KEY: Final[str] = "validation_errors"


_FENCE: Final[str] = r"(?:`{3}|´{3})"
_OPEN_FENCE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*{_FENCE}(?:json)?\s*\n?", re.IGNORECASE
)
_CLOSE_FENCE: Final[re.Pattern[str]] = re.compile(
    rf"(?:\n)?(?:json)?{_FENCE}\s*$", re.IGNORECASE
)


def _strip_outer_code_fences(s: str) -> str:
    """Remove at most one leading and one trailing Markdown code fence."""
    s = s.strip()
    m_open = _OPEN_FENCE.match(s)
    if m_open:
        s = s[m_open.end() :]
    m_close = _CLOSE_FENCE.search(s)
    if m_close:
        s = s[: m_close.start()]
    return s.strip()


def decode_raw_JSON(
    raw_response: str,
    labels_to_ignore: Sequence[str] = _THINK_TAGS,
) -> JSONDict:
    """Decode the first JSON object from a possibly fenced model response."""
    for tag in labels_to_ignore:
        raw_response = raw_response.split(tag)[-1]
    cleaned = _strip_outer_code_fences(raw_response).lstrip()
    decoder = json.JSONDecoder()
    try:
        decoded, i = decoder.raw_decode(cleaned)
    except JSONDecodeError:
        object_start = cleaned.find("{")
        if object_start <= 0:
            raise
        logger.warning(
            "LLM response had a non-JSON prefix; discarded %d characters.",
            object_start,
        )
        cleaned = cleaned[object_start:]
        decoded, i = decoder.raw_decode(cleaned)
    if not isinstance(decoded, dict):
        raise JSONDecodeError("Expected a JSON object", cleaned, 0)
    if cleaned[i:].strip().startswith(("{", "[")):
        logger.warning("LLM response had multiple JSON values; using the first.")
    return cast(JSONDict, decoded)


@overload
def get_completion(
    *,
    LlmOutputModel: None = None,
    active_tools: None = None,
    messages: list[Message],
    endpoint: EndpointLike | None = None,
    call_llm_api: _CompletionCaller | None = None,
    error_pipe: EventPipe | None = None,
    on_attempt_start: Callable[[], None] | None = None,
    tries: int = 5,
    include_raw_response_in_reprompt: bool = True,
    truncate_for_context: Callable[[list[Message]], list[Message]] = ...,
) -> str: ...


@overload
def get_completion(
    *,
    LlmOutputModel: type[BaseModel],
    active_tools: Sequence[AnyTool] | None = None,
    messages: list[Message],
    endpoint: EndpointLike | None = None,
    call_llm_api: _CompletionCaller | None = None,
    error_pipe: EventPipe | None = None,
    on_attempt_start: Callable[[], None] | None = None,
    tries: int = 5,
    include_raw_response_in_reprompt: bool = True,
    truncate_for_context: Callable[[list[Message]], list[Message]] = ...,
) -> JSONDict: ...


@overload
def get_completion(
    *,
    active_tools: Sequence[AnyTool],
    LlmOutputModel: type[BaseModel] | None = None,
    messages: list[Message],
    endpoint: EndpointLike | None = None,
    call_llm_api: _CompletionCaller | None = None,
    error_pipe: EventPipe | None = None,
    on_attempt_start: Callable[[], None] | None = None,
    tries: int = 5,
    include_raw_response_in_reprompt: bool = True,
    truncate_for_context: Callable[[list[Message]], list[Message]] = ...,
) -> JSONDict: ...


def get_completion(
    *,
    messages: list[Message],
    endpoint: EndpointLike | None = None,
    call_llm_api: _CompletionCaller | None = None,
    error_pipe: EventPipe | None = None,
    on_attempt_start: Callable[[], None] | None = None,
    active_tools: Sequence[AnyTool] | None = None,
    LlmOutputModel: type[BaseModel] | None = None,
    tries: int = 5,
    include_raw_response_in_reprompt: bool = True,
    truncate_for_context: Callable[
        [list[Message]], list[Message]
    ] = get_truncated_messages_for_context,
) -> str | JSONDict:
    """Return raw text, or validate a structured completion with repair retries.

    Supply exactly one of ``endpoint`` and ``call_llm_api``. An endpoint uses the
    standard non-streaming API implementation; a callback can customize transport,
    streaming, cancellation, or timeouts.

    With neither an output model nor a tool list, return the transport's text
    verbatim. Raw mode does not decode JSON or retry output-format errors, and
    telemetry is logged rather than included in the string. An explicit output
    model or tool list returns the existing validated dictionary with telemetry.
    Even an empty tool list requests structured action selection.

    ``LlmOutputModel`` validates responses; callers supply the prompt instructions
    describing the required JSON. ``error_pipe`` receives repair messages and is
    not forwarded as the provider execution pipe. Provider retries are owned by
    the transport; ``tries`` bounds completion attempts, including repairs.
    """
    if not messages:
        raise ValueError("Messages cannot be empty.")
    if (endpoint is None) == (call_llm_api is None):
        raise ValueError("Supply exactly one of endpoint and call_llm_api.")
    if endpoint is not None:
        call_llm_api = partial(_call_llm_api, endpoint)
    assert call_llm_api is not None

    updated_messages = messages.copy()
    if active_tools is None and LlmOutputModel is None and tries > 0:
        messages_to_send = truncate_for_context(updated_messages)
        if on_attempt_start is not None:
            on_attempt_start()
        raw_response, _ = call_llm_api(messages_to_send)
        return raw_response

    active_tools = active_tools if active_tools is not None else []
    retry_count = 0
    raw_response = ""
    while tries - retry_count > 0:
        retry_count += 1
        try:
            messages_to_send = truncate_for_context(updated_messages)
            if on_attempt_start is not None:
                on_attempt_start()
            raw_response, meta = call_llm_api(messages_to_send)
            parsed = _parse_response(raw_response, active_tools, LlmOutputModel)
            for key, value in meta.items():
                if value is not None:
                    parsed[key] = cast(JSONValue, value)
            return parsed
        except (
            KeyboardInterrupt,
            ExternalCallCancelledError,
            ExternalCallInterruptedError,
        ):
            raise
        except Exception as e:
            if isinstance(e, (NonexistentTool, JSONDecodeError, ValidationError)):
                diagnostic_data: dict[str, LogScalar] = {
                    _ATTEMPT_KEY: retry_count,
                    _MAX_ATTEMPTS_KEY: tries,
                    _ERROR_TYPE_KEY: type(e).__name__,
                    _RESPONSE_CHARS_KEY: len(raw_response),
                }
                if isinstance(e, JSONDecodeError):
                    diagnostic_data.update(
                        {
                            _LINE_KEY: e.lineno,
                            _COLUMN_KEY: e.colno,
                            _POSITION_KEY: e.pos,
                        }
                    )
                elif isinstance(e, ValidationError):
                    diagnostic_data[_VALIDATION_ERRORS_KEY] = e.error_count()
                content_state = (
                    ", response_chars=0"
                    if diagnostic_data[_RESPONSE_CHARS_KEY] == 0
                    else ""
                )
                log_with_data(
                    logger,
                    logging.WARNING,
                    (
                        "LLM response validation failed: "
                        f"attempt={retry_count}/{tries}, error={type(e).__name__}"
                        f"{content_state}"
                    ),
                    diagnostic_data,
                )
            updated_messages += _reprompt_on_error(
                raw_response=raw_response,
                error=e,
                pipe=error_pipe,
                include_raw_response=include_raw_response_in_reprompt,
            )
            continue
    logger.error("LLM response validation attempts exhausted (attempts=%d)", tries)
    raise LLMOutputFormatError


def _parse_response(
    raw_response: str,
    active_tools: Sequence[AnyTool],
    LlmOutputModel: type[BaseModel] | None,
) -> JSONDict:
    if active_tools and LlmOutputModel is not None:
        raise ValueError("active_tools and LlmOutputModel cannot both be given")
    decoded_response = decode_raw_JSON(raw_response)
    if LlmOutputModel is not None:
        _ = LlmOutputModel(**cast(dict[str, Any], decoded_response))
        return decoded_response
    if active_tools:
        _ = Invoke(**cast(dict[str, Any], decoded_response))
    for t in active_tools:
        if t.name == decoded_response.get(BaseNames.ACTION_FIELD):
            reduced_response = decoded_response.copy()
            del reduced_response[BaseNames.ACTION_FIELD]
            del reduced_response[BaseNames.RATIONALE_FIELD]
            _ = t.InputModel(**cast(dict[str, Any], reduced_response))

            return decoded_response
    raise NonexistentTool


_ERROR_PROMPTS: Mapping[type[Exception], str] = {
    NonexistentTool: ACTION_FIX_PROMPT,
    JSONDecodeError: JSON_FIX_PROMPT,
    ValidationError: PYDANTIC_FIX_PROMPT,
}


def _reprompt_on_error(
    raw_response: str,
    error: Exception,
    pipe: EventPipe | None = None,
    include_raw_response: bool = True,
    error_prompts: Mapping[type[Exception], str] = _ERROR_PROMPTS,
) -> list[Message]:
    error_to_raise: Exception | None = error
    fix_prompt: str | None = None
    for exc_type, prompt in error_prompts.items():
        if isinstance(error, exc_type):
            fix_prompt = prompt
            error_to_raise = None
            break
    if error_to_raise is not None:
        raise error_to_raise
    error_trace = f"{type(error).__name__}: {error!s}"
    if fix_prompt:
        previous = (
            f"Previous message: '{raw_response}'\n" if include_raw_response else ""
        )
        content = f"{previous}{fix_prompt}\n{error_trace}"
    else:
        content = error_trace
    message = Message(
        role=Role.ERROR, content=reduce_escapes(content), truncation=ERROR_RETRY
    )
    (pipe or EventPipe())(message)
    return [message]
