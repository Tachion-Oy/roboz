from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum, auto


class RuntimeEventCategory(StrEnum):
    LLM = auto()
    TOOL = auto()


class LifecycleKind(StrEnum):
    STARTED = auto()
    RETRYING = auto()
    SUCCEEDED = auto()


class FailureKind(StrEnum):
    FAILED = auto()
    TIMED_OUT = auto()
    CANCELLED = auto()
    INTERRUPTED = auto()


type RuntimeEventKind = LifecycleKind | FailureKind


class RuntimeEventLevel(StrEnum):
    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()

    @property
    def logging_level(self) -> int:
        return {
            RuntimeEventLevel.DEBUG: logging.DEBUG,
            RuntimeEventLevel.INFO: logging.INFO,
            RuntimeEventLevel.WARNING: logging.WARNING,
            RuntimeEventLevel.ERROR: logging.ERROR,
        }[self]


class ExternalCallPhase(StrEnum):
    WAIT = auto()
    WAITING_FOR_SLOT = "waiting-for-slot"
    AWAITING_RESULT = "awaiting-result"


@dataclass(frozen=True)
class ObservedFailure:
    """Typed failure lifecycle values shared by tool and LLM diagnostics."""

    kind: FailureKind
    level: RuntimeEventLevel

    @classmethod
    def failed(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        return cls(FailureKind.FAILED, level)

    @classmethod
    def timed_out(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        return cls(FailureKind.TIMED_OUT, level)

    @classmethod
    def cancelled(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        return cls(FailureKind.CANCELLED, level)

    @classmethod
    def interrupted(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        return cls(FailureKind.INTERRUPTED, level)


__all__ = [
    "ExternalCallPhase",
    "FailureKind",
    "LifecycleKind",
    "ObservedFailure",
    "RuntimeEventCategory",
    "RuntimeEventKind",
    "RuntimeEventLevel",
]
