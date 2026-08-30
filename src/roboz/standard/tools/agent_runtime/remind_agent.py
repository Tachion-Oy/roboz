"""Remind agent at pre-determined intervals with a fixed message."""

import json
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from roboz.models import All, Empty, Message, Str
from roboz.models import Severity, Truncation
from roboz.models import Role
from roboz import FactoryCtx, factory

NO_REMINDER: Final[str] = "The user did not respond"


@dataclass(frozen=True)
class RemindCtx(FactoryCtx):
    interval: int
    message: str
    flag: str


class Reminder(Empty):
    value: str
    flag: str


@factory
def remind_agent(input: All, messages: list[Message], ctx: RemindCtx) -> Str | Reminder:
    counter = 0
    for message in messages[::-1]:
        if counter >= ctx.interval:
            return Reminder(value=ctx.message, flag=ctx.flag)
        if message.role == Role.ASSISTANT:
            counter += 1
            continue
        try:
            content = json.loads(message.content)
            reminder = Reminder(**content)
            if reminder.flag == ctx.flag:
                break
        except (json.JSONDecodeError, ValidationError):
            continue
    return Str(
        value="No need for reminder",
        truncation=Truncation(threshold=0, severity=Severity.REMOVE),
    )


__all__ = ["RemindCtx", "Reminder", "remind_agent"]
