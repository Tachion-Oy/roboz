"""Notification text produced while agents load skills and tools."""

from __future__ import annotations

from typing import Final

from roboz.exceptions import (
    LLMAuthError,
    LLMContentFilteredError,
    LLMContextLimitExceededError,
    LLMImageInputError,
    LLMInsufficientFundsError,
    LLMProviderRequestError,
    LLMRateLimitExceededError,
)

AUTO_LOAD_SKILLS_BANNER: Final[str] = (
    "Here are skills that *I* have chosen to invoke automatically "
    "and the respective instructions on how to use "
    "them. They are from this point onwards freely at your disposal."
)
INTERRUPT_PROMPT_TO_USER: Final[str] = "Action interrupted!"
INTERRUPTED_GENERATION_CONTEXT: Final[str] = (
    "The previous assistant generation was interrupted by the user before it "
    "completed. Treat the user's next message as a change of direction."
)
NESTED_AGENT_NO_OUTCOME_PLACEHOLDER: Final[str] = "The agent finished without a message."


def provider_failure_notice(
    error: LLMProviderRequestError | LLMImageInputError,
) -> str:
    """Say which request failed, why, and what the user can do before replying.

    The text comes from the error's type alone. It never repeats the provider's
    own message.
    """
    match error:
        case LLMInsufficientFundsError():
            cause = "the provider account does not have enough credit for it"
            remedy = "Add credit or choose a model from another provider"
        case LLMAuthError():
            cause = "the provider rejected the API key"
            remedy = "Check the key or choose a model from another provider"
        case LLMRateLimitExceededError():
            cause = "the provider is limiting requests"
            remedy = "Wait a moment or choose another model"
        case LLMContextLimitExceededError():
            cause = "the conversation is longer than the model accepts"
            remedy = "Choose a model with a larger context window or start a new conversation"
        case LLMContentFilteredError():
            cause = "the provider's content filter stopped it"
            remedy = "Rephrase the request or choose another model"
        case LLMImageInputError():
            cause = "the model cannot take this image input"
            remedy = "Remove the image or choose another model"
        case _:
            cause = "the provider could not complete it"
            remedy = "Choose another model"
    if error.endpoint is None:
        request = "The request"
    else:
        request = f"The request to {error.endpoint}"
    return f"{request} failed: {cause}. {remedy}, then send a message to continue."


def auto_load_skill_rationale(skill_name: str) -> str:
    return f"Loading the {skill_name} skill"
