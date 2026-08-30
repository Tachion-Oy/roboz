"""Shared LLM execution for conversation summarization tools."""

import json
import logging
from typing import Final

from roboz.runtime import EventPipe
from roboz.models import Message, Str
from roboz.models import Role
from roboz.llm import EndpointLike, LLMEndpoint, MockLLMEndpoint
from roboz.llm import get_basic_system_prompt
from roboz.llm import call_llm_api, get_completion, resolve_endpoint

from .summary_prompts import (
    SUMMARY_OUTPUT_EXAMPLE,
    build_summary_length_feedback,
    build_summary_user_prompt,
)

logger = logging.getLogger(__name__)

SUMMARY_LENGTH_ATTEMPTS: Final[int] = 3


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
        ),
    ]


def _json_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    resolved = resolve_endpoint(endpoint)
    if isinstance(resolved, LLMEndpoint):
        return resolved.model_copy(update={"output_format": "json"})
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
    )["value"]
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
            content=json.dumps({"value": response}, ensure_ascii=False),
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
    timeout_s: float | None,
) -> str:
    attempts = 1 if max_chars is None else SUMMARY_LENGTH_ATTEMPTS
    shortest = ""
    for attempt in range(1, attempts + 1):
        response = _complete_summary(
            endpoint=endpoint,
            messages=messages,
            pipe=pipe,
            timeout_s=timeout_s,
        )
        if max_chars is None or len(response) <= max_chars:
            return response
        if not shortest or len(response) < len(shortest):
            shortest = response
        logger.warning(
            "Summary exceeded character limit (data=%s)",
            {
                "attempt": attempt,
                "max_attempts": attempts,
                "response_chars": len(response),
                "max_chars": max_chars,
            },
        )
        _append_length_feedback(
            messages=messages,
            pipe=pipe,
            response=response,
            max_chars=max_chars,
        )
    logger.warning(
        "Summary length attempts exhausted; returning shortest response (data=%s)",
        {
            "attempts": attempts,
            "shortest_chars": len(shortest),
            "max_chars": max_chars,
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
    timeout_s: float | None = None,
    pipe: EventPipe | None = None,
) -> str:
    """Summarize ``conversation``, retrying when the output exceeds ``max_chars``.

    With ``max_chars`` set, over-length responses are fed back to the LLM with a
    corrective message for up to ``SUMMARY_LENGTH_ATTEMPTS`` total attempts; the
    shortest attempt is returned if none complies. When ``pipe`` is supplied, its
    control signals cancel the provider wait and reject late completions.
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
        timeout_s=timeout_s,
    )


__all__ = ["summarize_conversation_segment"]
