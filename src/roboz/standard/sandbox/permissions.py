"""Permission policy for guarded operations."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto


class ActionVerdict(StrEnum):
    deny = auto()
    allow = auto()


class Operation(StrEnum):
    """Type of operation (also used as permission)."""

    READ = auto()
    CREATE = auto()
    DELETE = auto()


@dataclass
class PermissionRule:
    """Permission rule for a specific file pattern."""

    pattern: str | Callable[..., str]
    operations: set[Operation]

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("operations must not be empty")


__all__ = ["ActionVerdict", "Operation", "PermissionRule"]
