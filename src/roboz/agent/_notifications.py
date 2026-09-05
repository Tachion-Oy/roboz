"""Notification text produced while agents load skills and tools."""

from __future__ import annotations

from typing import Final

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
LLM_PROVIDER_REQUEST_RETRY_PROMPT: Final[str] = (
    "The selected model could not complete the request. Choose another model, "
    "then enter retry and Send."
)
SUBAGENT_NO_OUTCOME_PLACEHOLDER: Final[str] = "The agent finished without a message."


def auto_load_skill_rationale(skill_name: str) -> str:
    return f"Loading the {skill_name} skill"
