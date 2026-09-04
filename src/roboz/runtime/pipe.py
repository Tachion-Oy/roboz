from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock, current_thread
from typing import Any, Callable, Final, Literal, Sequence
from uuid import uuid4

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
)
from roboz.runtime._logging import LogScalar, log_with_data
from roboz.models import Message
from roboz.runtime.observability import (
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.runtime.events import (
    EventSink,
    MessageDeltaEvent,
    MessageEvent,
    PipeEvent,
    RunLifecycleEvent,
    RuntimeEvent,
    ScriptOutputEvent,
)
from roboz.runtime._external import ControlSignal
from roboz.runtime.persistence import RunStatus
from roboz.models import Role


def _get_active_agent_stack() -> tuple[str, ...]:
    """Resolve agent-owned execution context lazily to avoid import cycles."""
    from roboz.agent._execution_context import get_active_agent_stack

    return get_active_agent_stack()


logger = logging.getLogger(__name__)

_AGENT_KEY: Final[str] = "agent"
_PARENT_KEY: Final[str] = "parent"
_ENDPOINT_KEY: Final[str] = "endpoint"
_MODEL_KEY: Final[str] = "model"
_STATUS_KEY: Final[str] = "status"
_SEQUENCE_KEY: Final[str] = "sequence"
_THREAD_KEY: Final[str] = "thread"
_MESSAGE_ID_KEY: Final[str] = "message_id"
_CHUNK_KEY: Final[str] = "chunk"


class EventPipe:
    """Per-agent event dispatcher with optional persistence.

    Callers may pass ``event_sinks`` to attach explicit callbacks at construction
    time. The pipe does not infer terminal/API subscribers from output mode;
    callers compose those sinks before creating the pipe.
    """

    def __init__(
        self,
        *,
        event_sinks: Sequence[EventSink] | None = None,
    ):
        self.dry_run = False
        self._sinks: list[EventSink] = []
        self._sinks_lock = Lock()
        self._current_agent_name = "unknown"
        self._current_agent_description: str | None = None
        self._current_api_name: str | None = None
        self._current_model_name: str | None = None
        self._current_max_context_tokens: int | None = None
        self._current_temperature: float | None = None
        self._current_output_format: Literal["text", "json"] | None = None
        self._sequence = 0
        self._current_message_id: str | None = None
        self._current_message_chunk_index = 0
        self._cancel_signal = ControlSignal(
            error_type=ExternalCallCancelledError, message="External call cancelled"
        )
        self._interrupt_signal = ControlSignal(
            error_type=ExternalCallInterruptedError,
            message="External call interrupted",
        )
        for sink in event_sinks or ():
            self.add_sink(sink)

    @property
    def event_sinks(self) -> tuple[EventSink, ...]:
        """Return a snapshot of the currently attached event sinks."""
        with self._sinks_lock:
            return tuple(self._sinks)

    @property
    def data_path(self) -> Path | None:
        """Return persistence root if a persistence-capable sink is attached."""
        with self._sinks_lock:
            sinks = list(self._sinks)
        for sink in sinks:
            sink_data_path = getattr(sink, "data_path", None)
            if isinstance(sink_data_path, Path):
                return sink_data_path
            sink_data_path = getattr(sink, "_data_path", None)
            if isinstance(sink_data_path, Path):
                return sink_data_path
        return None

    def initialize(
        self,
        *,
        dry_run: bool | None = None,
        agent_name: str | None = None,
        agent_description: str | None = None,
        api_name: str | None = None,
        model_name: str | None = None,
        max_context_tokens: int | None = None,
        temperature: float | None = None,
        output_format: Literal["text", "json"] | None = None,
    ) -> None:
        if dry_run is not None:
            self.dry_run = dry_run
        self._current_agent_name = agent_name or "unknown"
        self._current_agent_description = agent_description
        self._sequence = 0
        self._current_api_name = api_name
        self._current_model_name = model_name
        self._current_max_context_tokens = max_context_tokens
        self._current_temperature = temperature
        self._current_output_format = output_format
        self._current_message_id = None
        self._current_message_chunk_index = 0
        self._cancel_signal.clear()
        self._interrupt_signal.clear()
        if self.dry_run:
            return
        self._sequence += 1
        stack_before = _get_active_agent_stack()
        parent_agent_name = stack_before[-1] if stack_before else None
        log_data: dict[str, LogScalar] = {
            _AGENT_KEY: self._current_agent_name,
            _PARENT_KEY: parent_agent_name,
            _ENDPOINT_KEY: api_name,
            _MODEL_KEY: model_name,
        }
        target = (
            f", endpoint={api_name}/{model_name}"
            if api_name is not None and model_name is not None
            else ""
        )
        log_with_data(
            logger,
            logging.INFO,
            (
                f"Agent started: {self._current_agent_name} "
                f"(parent={parent_agent_name or '-'}{target})"
            ),
            log_data,
        )
        self._emit(
            RunLifecycleEvent(
                kind="started",
                agent_name=self._current_agent_name,
                sequence=self._sequence,
                agent_description=self._current_agent_description,
                parent_agent_name=parent_agent_name,
                api_name=api_name,
                model_name=model_name,
                max_context_tokens=max_context_tokens,
                temperature=temperature,
                output_format=output_format,
            )
        )

    def finalize_run(self, *, status: RunStatus) -> None:
        """Emit final run status and ended lifecycle event."""
        if self.dry_run:
            return
        self._sequence += 1
        stack_before = _get_active_agent_stack()
        parent_agent_name = stack_before[-2] if len(stack_before) >= 2 else None
        log_with_data(
            logger,
            logging.INFO,
            f"Agent finished: {self._current_agent_name} (status={status})",
            {
                _AGENT_KEY: self._current_agent_name,
                _STATUS_KEY: str(status),
                _SEQUENCE_KEY: self._sequence,
            },
        )
        self._emit(
            RunLifecycleEvent(
                kind="stopped",
                agent_name=self._current_agent_name,
                sequence=self._sequence,
                agent_description=self._current_agent_description,
                parent_agent_name=parent_agent_name,
                status=status,
                api_name=self._current_api_name,
                model_name=self._current_model_name,
                max_context_tokens=self._current_max_context_tokens,
                temperature=self._current_temperature,
                output_format=self._current_output_format,
            )
        )

    def start_message(self) -> str:
        self._current_message_id = str(uuid4())
        self._current_message_chunk_index = 0
        return self._current_message_id

    def emit_message(self, message: Message) -> None:
        self._sequence += 1
        message_id = self._current_message_id
        if message_id is not None:
            self._current_message_id = None
            self._current_message_chunk_index = 0
        self._emit(MessageEvent(message, self._sequence, message_id=message_id))

    def emit_message_delta(self, delta: str) -> None:
        if self.dry_run:
            return
        if not delta:
            return
        if self._current_message_id is None:
            self.start_message()
        message_id = self._current_message_id
        assert message_id is not None
        self._current_message_chunk_index += 1
        self._sequence += 1
        self._emit(
            MessageDeltaEvent(
                message_id=message_id,
                delta=delta,
                chunk_index=self._current_message_chunk_index,
                role=Role.ASSISTANT,
                agent_name=self._current_agent_name,
                sequence=self._sequence,
            )
        )

    def emit_script_output(self, content: str) -> None:
        self._sequence += 1
        self._emit(ScriptOutputEvent(content=content, sequence=self._sequence))

    def emit_runtime_event(
        self,
        *,
        category: RuntimeEventCategory,
        kind: RuntimeEventKind,
        level: RuntimeEventLevel,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self.dry_run:
            return
        self._sequence += 1
        self._emit(
            RuntimeEvent(
                category=category,
                kind=kind,
                level=level,
                message=message,
                sequence=self._sequence,
                agent_name=self._current_agent_name,
                data=data,
            )
        )

    def add_sink(self, sink: EventSink) -> Callable[[], None]:
        with self._sinks_lock:
            self._sinks.append(sink)

        def _unsubscribe() -> None:
            self.unsubscribe(sink)

        return _unsubscribe

    @property
    def control_signals(self) -> tuple[ControlSignal, ControlSignal]:
        return (self._cancel_signal, self._interrupt_signal)

    @property
    def cancelled(self) -> bool:
        return self._cancel_signal.is_set

    def cancel(self) -> None:
        log_with_data(
            logger,
            logging.INFO,
            (
                f"Agent cancellation requested: {self._current_agent_name} "
                f"(sequence={self._sequence})"
            ),
            {
                _AGENT_KEY: self._current_agent_name,
                _SEQUENCE_KEY: self._sequence,
                _THREAD_KEY: current_thread().name,
            },
        )
        self._cancel_signal.set()

    def raise_if_cancelled(self) -> None:
        self._cancel_signal.raise_if_set()

    @property
    def interrupted(self) -> bool:
        return self._interrupt_signal.is_set

    def interrupt(self) -> None:
        # The message-index state is logged here because interrupt ordering is
        # exactly where the front-end/back-end index mismatch shows up: this is
        # the sequence/chunk the back-end believes it is at when the front-end
        # asks to interrupt.
        log_with_data(
            logger,
            logging.INFO,
            (
                f"Agent interrupt requested: {self._current_agent_name} "
                f"(sequence={self._sequence}, "
                f"chunk={self._current_message_chunk_index})"
            ),
            {
                _AGENT_KEY: self._current_agent_name,
                _SEQUENCE_KEY: self._sequence,
                _MESSAGE_ID_KEY: self._current_message_id,
                _CHUNK_KEY: self._current_message_chunk_index,
                _THREAD_KEY: current_thread().name,
            },
        )
        self._interrupt_signal.set()

    def clear_interrupt(self) -> None:
        logger.debug(
            "Interrupt cleared: agent=%s sequence=%d",
            self._current_agent_name,
            self._sequence,
        )
        self._interrupt_signal.clear()

    def raise_if_interrupted(self) -> None:
        self._interrupt_signal.raise_if_set()

    def unsubscribe(self, sink: EventSink) -> None:
        with self._sinks_lock:
            try:
                self._sinks.remove(sink)
            except ValueError:
                return

    def _emit(self, event: PipeEvent) -> None:
        with self._sinks_lock:
            sinks = list(self._sinks)
        for sink in sinks:
            sink(event)

    def __call__(self, message: Message) -> None:
        # The record (and every sink) gets the message verbatim. The message's
        # `truncation` is an agent-context concern, applied only when building the
        # LLM prompt in `get_completion` — it must not shape what is observed or
        # persisted.
        self.emit_message(message)
