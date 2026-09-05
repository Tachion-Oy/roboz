"""Lifecycle and external-call observation records."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum, auto


class RuntimeEventCategory(StrEnum):
    """Subsystem categories that emit runtime observations."""

    LLM = auto()
    TOOL = auto()


class LifecycleKind(StrEnum):
    """Successful lifecycle transitions for observed work."""

    STARTED = auto()
    RETRYING = auto()
    SUCCEEDED = auto()


class FailureKind(StrEnum):
    """Terminal failure transitions for observed work."""

    FAILED = auto()
    TIMED_OUT = auto()
    CANCELLED = auto()
    INTERRUPTED = auto()


type RuntimeEventKind = LifecycleKind | FailureKind


class RuntimeEventLevel(StrEnum):
    """Severity attached to a runtime observation."""

    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()

    @property
    def logging_level(self) -> int:
        """Return the corresponding standard-library logging level."""
        return {
            RuntimeEventLevel.DEBUG: logging.DEBUG,
            RuntimeEventLevel.INFO: logging.INFO,
            RuntimeEventLevel.WARNING: logging.WARNING,
            RuntimeEventLevel.ERROR: logging.ERROR,
        }[self]


class ExternalCallPhase(StrEnum):
    """Wait phases reported by cancellable external calls."""

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
        """Build a generic failure observation."""
        return cls(FailureKind.FAILED, level)

    @classmethod
    def timed_out(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        """Build a timeout failure observation."""
        return cls(FailureKind.TIMED_OUT, level)

    @classmethod
    def cancelled(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        """Build a cancellation failure observation."""
        return cls(FailureKind.CANCELLED, level)

    @classmethod
    def interrupted(
        cls, *, level: RuntimeEventLevel = RuntimeEventLevel.ERROR
    ) -> ObservedFailure:
        """Build an interruption failure observation."""
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
