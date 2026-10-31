"""Snapshot-specific normalization for persisted conversation messages."""

from typing import Final

from roboz.models import Message, MessageKind

_NO_DESCRIPTION: Final[str] = "no persisted agent description"
_STARTUP_CONTEXT_PLACEHOLDER: Final[str] = (
    "[startup context omitted: persistent memory injected]"
)
_UNKNOWN_SKILL: Final[str] = "unknown"


def normalize_messages_for_snapshot(
    messages: list[Message], *, agent_name: str, agent_description: str | None
) -> list[Message]:
    """Replace large bootstrap payloads with small prompt-visible markers."""
    normalized: list[Message] = []
    for message in messages:
        match message.message_kind:
            case MessageKind.AUTO_LOAD_BANNER:
                continue
            case MessageKind.SYSTEM_MESSAGE:
                description = agent_description or _NO_DESCRIPTION
                replacement = f"[system prompt omitted] {agent_name}: {description}"
            case MessageKind.STARTUP_CONTEXT:
                replacement = _STARTUP_CONTEXT_PLACEHOLDER
            case MessageKind.AUTO_LOADED_SKILL:
                skill_name = message.caller or _UNKNOWN_SKILL
                replacement = f"[auto-loaded skill omitted: {skill_name}]"
            case _:
                replacement = ""
        if replacement:
            normalized.append(
                Message(
                    role=message.role,
                    content=replacement,
                    truncation=message.truncation,
                    timestamp=message.timestamp,
                )
            )
            continue
        normalized.append(message)
    return normalized


__all__ = ["normalize_messages_for_snapshot"]
