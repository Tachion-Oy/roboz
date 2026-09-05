"""Runtime events, sinks, persistence, and interaction interfaces."""

from roboz.runtime._environment import load_key
from roboz.runtime._external import ControlSignal, run_cancellable_external_call
from roboz.runtime.events import (
    EventSink,
    MessageDeltaEvent,
    MessageEvent,
    PipeEvent,
    RunLifecycleEvent,
    RuntimeEvent,
    ScriptOutputEvent,
)
from roboz.runtime.io import (
    Output,
    UserIO,
    bind_api_user_io,
    bind_output,
    get_bound_output,
    interact_with_user,
    reset_api_user_io,
    reset_output,
)
from roboz.runtime._logging import (
    LOG_DATA_ATTRIBUTE,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LogScalar,
    log_with_data,
    reset_logging_config,
)
from roboz.runtime.observability import (
    ExternalCallPhase,
    FailureKind,
    LifecycleKind,
    ObservedFailure,
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.runtime.persistence import RunStatus
from roboz.runtime.pipe import EventPipe
from roboz.runtime.sinks import CliSink, PersistenceSink, default_event_sinks

__all__ = [
    "CliSink",
    "ControlSignal",
    "EventPipe",
    "EventSink",
    "ExternalCallPhase",
    "FailureKind",
    "LifecycleKind",
    "LOG_DATA_ATTRIBUTE",
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "LogScalar",
    "MessageDeltaEvent",
    "MessageEvent",
    "ObservedFailure",
    "Output",
    "PersistenceSink",
    "PipeEvent",
    "RunLifecycleEvent",
    "RunStatus",
    "RuntimeEvent",
    "RuntimeEventCategory",
    "RuntimeEventKind",
    "RuntimeEventLevel",
    "ScriptOutputEvent",
    "UserIO",
    "bind_api_user_io",
    "bind_output",
    "default_event_sinks",
    "get_bound_output",
    "interact_with_user",
    "load_key",
    "log_with_data",
    "reset_api_user_io",
    "reset_output",
    "reset_logging_config",
    "run_cancellable_external_call",
]
