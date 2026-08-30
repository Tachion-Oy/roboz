"""Normalize persisted messages for the conversation snapshot tool."""

import json

from roboz.models import Message
from roboz.models import BaseNames, MessageKind


def _payload(message: Message) -> dict | None:
    try:
        data = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_messages_for_snapshot(
    messages: list[Message], *, agent_name: str, agent_description: str | None
) -> list[Message]:
    out: list[Message] = []
    for message in messages:
        payload = _payload(message)
        kind = message.message_kind

        match kind:
            case MessageKind.AUTO_LOAD_BANNER:
                continue
            case MessageKind.SYSTEM_MESSAGE:
                description = agent_description or "no persisted agent description"
                override = f"[system prompt omitted] {agent_name}: {description}"
            case MessageKind.STARTUP_CONTEXT:
                override = "[startup context omitted: persistent memory injected]"
            case MessageKind.AUTO_LOADED_SKILL:
                skill = (
                    payload.get(BaseNames.CALLER_FIELD) if payload is not None else None
                )
                override = f"[auto-loaded skill omitted: {skill or 'unknown'}]"
            case _:
                override = ""
        if override:
            out.append(
                Message(
                    role=message.role, content=override, truncation=message.truncation
                )
            )
            continue
        out.append(message)
    return out


__all__ = ["normalize_messages_for_snapshot"]
