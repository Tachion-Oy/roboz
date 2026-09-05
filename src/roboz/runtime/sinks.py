"""Terminal, persistence, and callback sinks for runtime events."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from roboz.models import Message, Role
from roboz.runtime._paths import get_conversation_run_path
from roboz.runtime.events import (
    EventSink,
    MessageDeltaEvent,
    MessageEvent,
    PipeEvent,
    RunLifecycleEvent,
    RuntimeEvent,
    ScriptOutputEvent,
)
from roboz.runtime.observability import RuntimeEventLevel
from roboz.runtime.persistence import (
    ConversationRun,
    RunMetadata,
    RunStatus,
    atomic_write_json,
    clear_conversation_active,
    mark_conversation_active,
    message_to_logged_row,
    runtime_event_to_logged_row,
    utc_iso_z,
)

_ROLE_COLORS: dict[Role, str] = {
    Role.SYSTEM: "dark_orange",
    Role.ASSISTANT: "chartreuse3",
    Role.USER: "yellow1",
    Role.ERROR: "red1",
}


class CliSink:
    """Render selected pipe events to a Rich terminal console."""

    DEFAULT_TYPES_TO_PRINT: frozenset[Role] = frozenset(
        {Role.USER, Role.SYSTEM, Role.ASSISTANT, Role.ERROR}
    )

    def __init__(self, *, types_to_print: set[Role]):
        """Initialize a terminal sink with the roles it should display."""
        self._types_to_print = types_to_print

    @classmethod
    def default(cls) -> "CliSink":
        """Create the standard terminal sink for message and lifecycle events."""
        return cls(types_to_print=set(cls.DEFAULT_TYPES_TO_PRINT))

    def __call__(self, event: PipeEvent) -> None:
        """Render one supported pipe event."""
        match event:
            case MessageEvent(message=message):
                rich_print_message_to_terminal(message, self._types_to_print)
            case ScriptOutputEvent(content=content):
                Console().print(escape(content))
            case MessageDeltaEvent():
                return
            case RuntimeEvent(level=RuntimeEventLevel.ERROR):
                Console().print(
                    f"[red][bold]Runtime:[/bold] {escape(event.message)}[/red]"
                )
            case RuntimeEvent():
                return
            case RunLifecycleEvent(kind="started"):
                self._print_framed_banner(self._format_started_banner(event))
            case RunLifecycleEvent(kind="stopped", agent_name=agent_name):
                self._print_framed_banner(f"Agent «{agent_name}» — stopped")

    @staticmethod
    def _print_framed_banner(message: str, *, border_style: str = "cyan") -> None:
        Console().print(
            Panel.fit(message, border_style=border_style, box=box.HEAVY_EDGE)
        )

    @staticmethod
    def _format_started_banner(event: RunLifecycleEvent) -> str:
        details: list[str] = []
        endpoint_pair = " / ".join(
            p for p in [event.api_name, event.model_name] if p is not None
        )
        if endpoint_pair:
            details.append(endpoint_pair)
        if event.max_context_tokens is not None:
            details.append(f"ctx={event.max_context_tokens}")
        if event.temperature is not None:
            details.append(f"temp={event.temperature}")
        if event.output_format is not None:
            details.append(f"fmt={event.output_format}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"Agent «{event.agent_name}» — invoked{suffix}"


PersistenceDetail = MessageEvent | RuntimeEvent
PersistenceDetailPredicate = Callable[[PersistenceDetail], bool]


class PersistenceSink:
    """Persist run lifecycle, messages, and runtime observations as JSON."""

    def __init__(
        self,
        data_path: Path,
        *,
        detail_predicate: PersistenceDetailPredicate | None = None,
    ):
        """Initialize a sink rooted at a directory with an optional detail filter."""
        if data_path.exists() and not data_path.is_dir():
            raise ValueError("data_path must be a folder")
        self._data_path = data_path
        self._detail_predicate = detail_predicate
        self.conversations_location: Path | None = None
        self._conversation_run: ConversationRun | None = None

    @classmethod
    def for_path(
        cls,
        data_path: Path,
        *,
        detail_predicate: PersistenceDetailPredicate | None = None,
    ) -> "PersistenceSink":
        """Create a sink, optionally filtering message/runtime details.

        Run lifecycle events always persist so status remains authoritative.
        """
        return cls(data_path, detail_predicate=detail_predicate)

    @property
    def data_path(self) -> Path:
        """Return the persistence root directory."""
        return self._data_path

    def __call__(self, event: PipeEvent) -> None:
        """Persist the applicable portion of one pipe event."""
        match event:
            case MessageEvent():
                if not self._should_persist_detail(event):
                    return
                self._save_message(event)
            case ScriptOutputEvent() | MessageDeltaEvent():
                return
            case RuntimeEvent():
                if not self._should_persist_detail(event):
                    return
                self._save_runtime_event(event)
            case RunLifecycleEvent(kind="started"):
                self._start_run(event)
            case RunLifecycleEvent(kind="stopped", status=status):
                self._stop_run(status)

    def _should_persist_detail(self, event: PersistenceDetail) -> bool:
        return self._detail_predicate is None or self._detail_predicate(event)

    def reset_run(self) -> None:
        """Clear the currently active persisted run state."""
        self.conversations_location = None
        self._conversation_run = None

    def _start_run(self, event: RunLifecycleEvent) -> None:
        self.reset_run()
        cid = str(uuid4())
        started = datetime.now(timezone.utc)
        self.conversations_location = get_conversation_run_path(
            self._data_path, started_at=started, conversation_id=cid
        )
        self._conversation_run = ConversationRun(
            conversation_id=cid,
            agent_name=event.agent_name,
            started_at=utc_iso_z(started),
            metadata=RunMetadata(agent_description=event.agent_description),
        )
        self._write_run(self.conversations_location)
        mark_conversation_active(
            agent_dir=self._data_path,
            conversation_id=self._conversation_run.conversation_id,
        )

    def _save_message(self, event: MessageEvent) -> None:
        run = self._conversation_run
        path = self.conversations_location
        if run is None or path is None:
            return
        row = message_to_logged_row(
            event.message,
            message_id=str(uuid4()),
            sequence=event.sequence,
            created_at=datetime.now(timezone.utc),
        )
        run.messages.append(row)
        self._write_run(path)

    def _save_runtime_event(self, event: RuntimeEvent) -> None:
        run = self._conversation_run
        path = self.conversations_location
        if run is None or path is None:
            return
        row = runtime_event_to_logged_row(
            event,
            event_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
        )
        run.runtime_events.append(row)
        self._write_run(path)

    def _stop_run(self, status: RunStatus | None) -> None:
        run = self._conversation_run
        path = self.conversations_location
        if run is None or path is None:
            return
        if status is None:
            return
        run.status = status
        run.ended_at = utc_iso_z(datetime.now(timezone.utc))
        self._write_run(path)
        clear_conversation_active(
            agent_dir=self._data_path,
            conversation_id=run.conversation_id,
        )

    def _write_run(self, path: Path) -> None:
        run = self._conversation_run
        assert run is not None
        atomic_write_json(path, run.model_dump(mode="json"))


def rich_print_message_to_terminal(
    message: Message,
    types_to_print: set[Role],
    console=Console(),
):
    """Render a message when its role is enabled for terminal output."""
    if message.role not in types_to_print:
        return
    color = _ROLE_COLORS.get(message.role, "white")
    console.print(
        f"[{color}][bold]{message.role.capitalize()}:[/bold] {escape(message.content)}[/{color}]"
    )


def default_event_sinks(
    *,
    data_path: Path | None = None,
    include_cli: bool = True,
    persistence_detail_predicate: PersistenceDetailPredicate | None = None,
) -> tuple[EventSink, ...]:
    """Create the standard explicit sinks for an agent.

    This is a convenience for callers that want Agent's built-in terminal and
    persistence sinks without making :class:`~roboz.runtime.pipe.EventPipe`
    infer them from ``Output`` or ``data_path``. The returned tuple is meant to be
    passed to ``Agent(event_sinks=...)``. ``persistence_detail_predicate`` applies
    only to message and runtime-event details, never run lifecycle events.
    """
    sinks: list[EventSink] = []
    if include_cli:
        sinks.append(CliSink.default())
    if data_path is not None:
        sinks.append(
            PersistenceSink.for_path(
                data_path,
                detail_predicate=persistence_detail_predicate,
            )
        )
    return tuple(sinks)
