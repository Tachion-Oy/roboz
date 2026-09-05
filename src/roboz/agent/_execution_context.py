"""Context-local tracking of nested agent execution."""

from __future__ import annotations

import keyword
from contextvars import ContextVar, Token


_active_agent_stack: ContextVar[tuple[str, ...]] = ContextVar(
    "_active_agent_stack", default=()
)


def get_active_agent_stack() -> tuple[str, ...]:
    return _active_agent_stack.get()


def push_active_agent(agent_name: str) -> Token[tuple[str, ...]]:
    stack = _active_agent_stack.get()
    return _active_agent_stack.set(stack + (agent_name,))


def pop_active_agent(token: Token[tuple[str, ...]]) -> None:
    _active_agent_stack.reset(token)


_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


def validate_agent_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"agent name must be str, got {type(name).__name__}")
    stripped = name.strip()
    if not stripped:
        raise ValueError("agent name cannot be blank")
    if not stripped.isidentifier():
        raise ValueError(f"agent name must be a valid Python identifier, got {name!r}")
    if keyword.iskeyword(stripped):
        raise ValueError(f"agent name cannot be a Python keyword, got {name!r}")
    if stripped.lower() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(
            f"agent name is reserved as a device name on Windows, got {name!r}"
        )
    return stripped
