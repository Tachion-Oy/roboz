from __future__ import annotations

import time
from logging import getLogger
from typing import Any

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMProviderRequestError,
)
from roboz.models import Empty, Invoke, Message, Stop
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
                "duration_ms": round((time.monotonic() - started) * 1000),
                "output_type": type(output).__name__,
                "output_chars": output_chars,
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
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error_type": type(error).__name__,
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
        observation = {"tool": tool.name, **(data or {})}
        logger.log(
            level.logging_level,
            "%s (agent=%s, kind=%s, data=%s)",
            message,
            self._agent_name,
            kind,
            observation,
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
