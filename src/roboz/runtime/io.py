from __future__ import annotations

import select
import sys
from contextvars import ContextVar, Token
from enum import Enum, auto
from typing import Protocol


class Output(Enum):
    CLI = auto()
    GUI = auto()
    BOT = auto()
    API = auto()


class UserIO(Protocol):
    """Host-provided user interaction for :class:`Output.API`."""

    def request_input(self, message: str, timeout: float | None = None) -> str | None:
        """Block until the user replies, or until *timeout* seconds elapse."""
        ...

    def notify(self, message: str) -> None:
        """Show *message* without waiting for a reply."""
        ...


_current_user_io: ContextVar[UserIO | None] = ContextVar(
    "_current_user_io", default=None
)
_current_output: ContextVar[Output | None] = ContextVar("_current_output", default=None)


def bind_api_user_io(user_io: UserIO) -> Token[UserIO | None]:
    return _current_user_io.set(user_io)


def reset_api_user_io(token: Token[UserIO | None]) -> None:
    _current_user_io.reset(token)


def bind_output(output: Output) -> Token[Output | None]:
    return _current_output.set(output)


def reset_output(token: Token[Output | None]) -> None:
    _current_output.reset(token)


def get_bound_output(default: Output | None = None) -> Output | None:
    bound = _current_output.get()
    return default if bound is None else bound


def _require_api_user_io() -> UserIO:
    user_io = _current_user_io.get()
    if user_io is None:
        raise RuntimeError(
            "Output.API requires a UserIO bound via roboz.runtime.io._current_user_io"
        )
    return user_io


def _require_bound_output() -> Output:
    output = _current_output.get()
    if output is None:
        raise RuntimeError(
            "Runtime output is not bound; call roboz.runtime.bind_output before invoke"
        )
    return output


def _read_line_with_timeout(timeout: float | None) -> str | None:
    if timeout is None:
        return sys.stdin.readline()
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.readline() if ready else None


def interact_with_user(
    message: str, with_reply: bool, timeout: float | None = None
) -> str | None:
    output = _require_bound_output()
    match output:
        case Output.CLI:
            print(message)
            if not with_reply:
                return None
            return _read_line_with_timeout(timeout)
        case Output.API:
            user_io = _require_api_user_io()
            if with_reply:
                return user_io.request_input(message, timeout)
            user_io.notify(message)
            return None
        case _:
            raise ValueError(f"The output {output} is not yet implemented")
