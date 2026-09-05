"""Lifecycle observation around individual tool calls."""

from __future__ import annotations

import time
from logging import getLogger
from typing import Any, Final

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMProviderRequestError,
)
from roboz.models import Empty, Invoke, Message, Stop
from roboz.runtime._logging import LogScalar, log_with_data
from roboz.runtime.observability import (
    LifecycleKind,
    ObservedFailure,
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.runtime.pipe import EventPipe
from roboz.tooling.core import Tool

logger = getLogger(__name__)

_AGENT_KEY: Final[str] = "agent"
_TOOL_KEY: Final[str] = "tool"
_DURATION_MS_KEY: Final[str] = "duration_ms"
_OUTPUT_TYPE_KEY: Final[str] = "output_type"
_OUTPUT_CHARS_KEY: Final[str] = "output_chars"
_ERROR_TYPE_KEY: Final[str] = "error_type"
_MILLISECONDS_PER_SECOND: Final[int] = 1_000


class ToolInvocationObserver:
    """Invoke tools while emitting their agent-scoped runtime lifecycle.

    This wrapper is observability-only. It does not recover from, translate, or
    otherwise handle failures; after recording one, it re-raises the original
    exception unchanged so the agent retains ownership of control-flow policy.
    """

    def __init__(self, *, agent_name: str, pipe: EventPipe) -> None:
        self._agent_name = agent_name
        self._pipe = pipe

    def invoke(
        self,
        tool: Tool[Any, Any],
        input: Invoke | Empty,
        messages: list[Message],
    ) -> Invoke | Empty | Stop:
        started = time.monotonic()
        self._emit(
            tool=tool,
            kind=LifecycleKind.STARTED,
            level=RuntimeEventLevel.INFO,
            message="Tool call started",
        )
        try:
            output = tool(input, messages)
        except (Exception, KeyboardInterrupt) as error:
            self._emit_failure(tool=tool, error=error, started=started)
            raise

        output_chars = len(output.model_dump_json())
        self._emit(
            tool=tool,
            kind=LifecycleKind.SUCCEEDED,
            level=RuntimeEventLevel.INFO,
            message="Tool call succeeded",
            data={
                _DURATION_MS_KEY: round(
                    (time.monotonic() - started) * _MILLISECONDS_PER_SECOND
                ),
                _OUTPUT_TYPE_KEY: type(output).__name__,
                _OUTPUT_CHARS_KEY: output_chars,
            },
        )
        return output

    def _emit_failure(
        self,
        *,
        tool: Tool[Any, Any],
        error: BaseException,
        started: float,
    ) -> None:
        failure = _failure_for(error)
        self._emit(
            tool=tool,
            kind=failure.kind,
            level=failure.level,
            message=f"Tool call {failure.kind}",
            data={
                _DURATION_MS_KEY: round(
                    (time.monotonic() - started) * _MILLISECONDS_PER_SECOND
                ),
                _ERROR_TYPE_KEY: type(error).__name__,
            },
        )

    def _emit(
        self,
        *,
        tool: Tool[Any, Any],
        kind: RuntimeEventKind,
        level: RuntimeEventLevel,
        message: str,
        data: dict[str, object] | None = None,
    ) -> None:
        observation = {_TOOL_KEY: tool.name, **(data or {})}
        log_data: dict[str, LogScalar] = {_AGENT_KEY: self._agent_name}
        log_data.update(
            (key, value)
            for key, value in observation.items()
            if value is None or isinstance(value, (str, int, float, bool))
        )
        details: list[str] = []
        duration_ms = observation.get(_DURATION_MS_KEY)
        if isinstance(duration_ms, int):
            details.append(f"duration_ms={duration_ms}")
        output_type = observation.get(_OUTPUT_TYPE_KEY)
        if isinstance(output_type, str):
            details.append(f"result={output_type}")
        error_type = observation.get(_ERROR_TYPE_KEY)
        if isinstance(error_type, str):
            details.append(f"error_type={error_type}")
        suffix = f", {', '.join(details)}" if details else ""
        log_with_data(
            logger,
            level.logging_level,
            f"Tool call {kind}: {tool.name} (agent={self._agent_name}{suffix})",
            log_data,
        )
        self._pipe.emit_runtime_event(
            category=RuntimeEventCategory.TOOL,
            kind=kind,
            level=level,
            message=f"{message}: {tool.name}",
            data=observation,
        )


def _failure_for(error: BaseException) -> ObservedFailure:
    match error:
        case LLMProviderRequestError():
            return ObservedFailure.failed(level=RuntimeEventLevel.WARNING)
        case ExternalCallInterruptedError() | KeyboardInterrupt():
            return ObservedFailure.interrupted(level=RuntimeEventLevel.WARNING)
        case ExternalCallCancelledError():
            return ObservedFailure.cancelled(level=RuntimeEventLevel.WARNING)
        case _:
            return ObservedFailure.failed()


__all__ = ["ToolInvocationObserver"]
