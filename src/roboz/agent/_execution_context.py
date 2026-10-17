"""Context-local tracking of nested agent execution."""

from __future__ import annotations

import keyword
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roboz.llm.endpoints import EndpointLike


_agent_endpoint: ContextVar[Callable[[], EndpointLike | None] | None] = ContextVar(
    "_agent_endpoint", default=None
)


def bind_agent_endpoint(
    getter: Callable[[], EndpointLike | None],
) -> Token[Callable[[], EndpointLike | None] | None]:
    """Bind the owning agent's live endpoint for tools in this execution context."""
    return _agent_endpoint.set(getter)


def reset_agent_endpoint(token: Token[Callable[[], EndpointLike | None] | None]) -> None:
    """Restore the parent agent's endpoint after an invocation."""
    _agent_endpoint.reset(token)


def get_active_agent_endpoint() -> EndpointLike | None:
    """Return the current owner's configured endpoint without materializing it."""
    getter = _agent_endpoint.get()
    return getter() if getter is not None else None


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
