"""Shared LLM summarization for compactification and memory artifacts."""

import json
import logging
import math
from typing import Final

from roboshed.tools.compactification.summary_prompts import (
    SUMMARY_OUTPUT_EXAMPLE,
    build_summary_length_feedback,
    build_summary_user_prompt,
)
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    call_llm_api,
    get_basic_system_prompt,
    get_completion,
    resolve_endpoint,
)
from roboz.models import NO_TRUNCATION, BaseNames, Message, Role, Str
from roboz.runtime import EventPipe, log_with_data

logger = logging.getLogger(__name__)

SUMMARY_LENGTH_ATTEMPTS: Final[int] = 3
DEFAULT_MAX_CHARS_TOLERANCE_PERCENT: Final[float] = 15.0

_PERCENT_SCALE: Final[int] = 100
_FIRST_ATTEMPT: Final[int] = 1
_MIN_TOLERANCE_PERCENT: Final[float] = 0.0
_OUTPUT_FORMAT_FIELD: Final[str] = "output_format"
_JSON_OUTPUT_FORMAT: Final[str] = "json"
_RESPONSE_CHARS_KEY: Final[str] = "response_chars"
_REQUESTED_MAX_CHARS_KEY: Final[str] = "requested_max_chars"
_ACCEPTED_MAX_CHARS_KEY: Final[str] = "accepted_max_chars"
_TOLERANCE_PERCENT_KEY: Final[str] = "tolerance_percent"
_ATTEMPT_KEY: Final[str] = "attempt"
_MAX_ATTEMPTS_KEY: Final[str] = "max_attempts"
_ATTEMPTS_KEY: Final[str] = "attempts"
_SHORTEST_CHARS_KEY: Final[str] = "shortest_chars"


def _validate_tolerance_percent(tolerance_percent: float) -> None:
    if (
        not math.isfinite(tolerance_percent)
        or tolerance_percent < _MIN_TOLERANCE_PERCENT
    ):
        raise ValueError("max_chars_tolerance_percent must be finite and non-negative")


def _accepted_max_chars(*, max_chars: int, tolerance_percent: float) -> int:
    _validate_tolerance_percent(tolerance_percent)
    return math.ceil(
        max_chars * (_PERCENT_SCALE + tolerance_percent) / _PERCENT_SCALE
    )


def _initial_messages(
    *,
    system_prompt: str,
    instructions: str,
    conversation: str,
    max_chars: int | None,
) -> list[Message]:
    prompt = get_basic_system_prompt(
        system_prompt=system_prompt,
        OutputModel=Str,
        output_example=SUMMARY_OUTPUT_EXAMPLE,
    )
    return [
        Message(role=Role.SYSTEM, content=prompt),
        Message(
            role=Role.USER,
            content=build_summary_user_prompt(
                instructions=instructions,
                conversation=conversation,
                max_chars=max_chars,
            ),
            truncation=NO_TRUNCATION,
        ),
    ]


def _json_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    resolved = resolve_endpoint(endpoint)
    if isinstance(resolved, LLMEndpoint):
        return resolved.model_copy(
            update={_OUTPUT_FORMAT_FIELD: _JSON_OUTPUT_FORMAT}
        )
    return resolved


def _complete_summary(
    *,
    endpoint: LLMEndpoint | MockLLMEndpoint,
    messages: list[Message],
    pipe: EventPipe | None,
    timeout_s: float | None,
) -> str:
    value = get_completion(
        messages=messages,
        LlmOutputModel=Str,
        error_pipe=pipe,
        include_raw_response_in_reprompt=False,
        call_llm_api=lambda request: call_llm_api(
            endpoint, request, pipe=pipe, timeout_s=timeout_s
        ),
    )[BaseNames.VALUE_FIELD]
    if pipe is not None:
        pipe.raise_if_cancelled()
    return value if isinstance(value, str) else str(value)


def _append_length_feedback(
    *,
    messages: list[Message],
    pipe: EventPipe | None,
    response: str,
    max_chars: int,
) -> None:
    feedback = [
        Message(
            role=Role.ASSISTANT,
            content=json.dumps(
                {BaseNames.VALUE_FIELD: response}, ensure_ascii=False
            ),
        ),
        Message(
            role=Role.USER,
            content=build_summary_length_feedback(
                response_chars=len(response), max_chars=max_chars
            ),
        ),
    ]
    messages.extend(feedback)
    if pipe is not None:
        for message in feedback:
            pipe(message)


def _complete_with_length_limit(
    *,
    endpoint: LLMEndpoint | MockLLMEndpoint,
    messages: list[Message],
    pipe: EventPipe | None,
    max_chars: int | None,
    max_chars_tolerance_percent: float,
    timeout_s: float | None,
) -> str:
    _validate_tolerance_percent(max_chars_tolerance_percent)
    accepted_max_chars = (
        None
        if max_chars is None
        else _accepted_max_chars(
            max_chars=max_chars,
            tolerance_percent=max_chars_tolerance_percent,
        )
    )
    attempts = _FIRST_ATTEMPT if max_chars is None else SUMMARY_LENGTH_ATTEMPTS
    shortest = ""
    for attempt in range(_FIRST_ATTEMPT, attempts + _FIRST_ATTEMPT):
        response = _complete_summary(
            endpoint=endpoint,
            messages=messages,
            pipe=pipe,
            timeout_s=timeout_s,
        )
        if accepted_max_chars is None or len(response) <= accepted_max_chars:
            if max_chars is not None and len(response) > max_chars:
                log_with_data(
                    logger,
                    logging.INFO,
                    (
                        "Summary accepted within tolerance "
                        f"(response_chars={len(response)}, "
                        f"requested_max_chars={max_chars})"
                    ),
                    data={
                        _RESPONSE_CHARS_KEY: len(response),
                        _REQUESTED_MAX_CHARS_KEY: max_chars,
                        _ACCEPTED_MAX_CHARS_KEY: accepted_max_chars,
                        _TOLERANCE_PERCENT_KEY: max_chars_tolerance_percent,
                    },
                )
            return response
        assert max_chars is not None
        if not shortest or len(response) < len(shortest):
            shortest = response
        log_with_data(
            logger,
            logging.WARNING,
            (
                "Summary exceeded character limit "
                f"(attempt={attempt}/{attempts}, response_chars={len(response)}, "
                f"requested_max_chars={max_chars})"
            ),
            data={
                _ATTEMPT_KEY: attempt,
                _MAX_ATTEMPTS_KEY: attempts,
                _RESPONSE_CHARS_KEY: len(response),
                _REQUESTED_MAX_CHARS_KEY: max_chars,
                _ACCEPTED_MAX_CHARS_KEY: accepted_max_chars,
                _TOLERANCE_PERCENT_KEY: max_chars_tolerance_percent,
            },
        )
        _append_length_feedback(
            messages=messages,
            pipe=pipe,
            response=response,
            max_chars=max_chars,
        )
    log_with_data(
        logger,
        logging.WARNING,
        (
            "Summary length attempts exhausted "
            f"(attempts={attempts}, shortest_chars={len(shortest)})"
        ),
        data={
            _ATTEMPTS_KEY: attempts,
            _SHORTEST_CHARS_KEY: len(shortest),
            _REQUESTED_MAX_CHARS_KEY: max_chars,
            _ACCEPTED_MAX_CHARS_KEY: accepted_max_chars,
            _TOLERANCE_PERCENT_KEY: max_chars_tolerance_percent,
        },
    )
    return shortest


def summarize_conversation_segment(
    *,
    endpoint: EndpointLike,
    system_prompt: str,
    instructions: str,
    conversation: str,
    max_chars: int | None = None,
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
    timeout_s: float | None = None,
    pipe: EventPipe | None = None,
) -> str:
    """Summarize ``conversation``, retrying when output exceeds ``max_chars``.

    Responses inside ``max_chars_tolerance_percent`` over the requested budget
    are accepted. Larger responses receive corrective feedback for at most
    ``SUMMARY_LENGTH_ATTEMPTS`` total attempts; the shortest attempt is returned
    if none complies. The complete source conversation is deliberately marked
    ``NO_TRUNCATION`` so recent evidence is never silently cut before summary.
    """
    normalized_instructions = instructions.strip()
    if not normalized_instructions:
        raise ValueError("instructions must be a non-empty string")

    llm_messages = _initial_messages(
        system_prompt=system_prompt,
        instructions=normalized_instructions,
        conversation=conversation,
        max_chars=max_chars,
    )
    if pipe is not None:
        for message in llm_messages:
            pipe(message)

    return _complete_with_length_limit(
        endpoint=_json_endpoint(endpoint),
        messages=llm_messages,
        pipe=pipe,
        max_chars=max_chars,
        max_chars_tolerance_percent=max_chars_tolerance_percent,
        timeout_s=timeout_s,
    )


__all__ = [
    "DEFAULT_MAX_CHARS_TOLERANCE_PERCENT",
    "SUMMARY_LENGTH_ATTEMPTS",
    "summarize_conversation_segment",
]
