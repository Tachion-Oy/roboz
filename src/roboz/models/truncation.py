from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Final, TypeAlias

LIGHT_MAX_CHARS: Final[int] = 40_000
GRADED_STUB_DISTANCE: Final[int] = 50
GRADED_REMOVE_DISTANCE: Final[int] = 200
ERROR_RETRY_DISTANCE: Final[int] = 5


class Severity(IntEnum):
    """How aggressively a message's content is cut for LLM context.

    LIGHT  - cap every string field at LIGHT_MAX_CHARS characters; structure kept.
    STUB   - collapse to a placeholder (plus caller/action); body discarded.
    REMOVE - drop the message from context entirely.
    """

    LIGHT = auto()
    STUB = auto()
    REMOVE = auto()


@dataclass(frozen=True, slots=True)
class Truncation:
    threshold: int
    severity: Severity

    @classmethod
    def to_rules_list(cls, spec: "TruncationSpec") -> list["Truncation"]:
        """Single rule or graded list → list (shallow copy if already a list)."""
        if isinstance(spec, cls):
            return [spec]
        assert isinstance(spec, list)
        return list(spec)


TruncationSpec: TypeAlias = Truncation | list[Truncation]

# Truncation applies at every distance >= threshold; the rule with the largest
# satisfied threshold wins (see select_truncation). threshold=-1 matches nothing.

# threshold=0 so LIGHT applies at every distance (incl. the freshest message):
# no message may carry an unbounded payload into the LLM context by default.
DEFAULT = Truncation(threshold=0, severity=Severity.LIGHT)
# Explicit opt-out: never truncate. Use deliberately when a message must stay full.
NO_TRUNCATION = Truncation(threshold=-1, severity=Severity.LIGHT)
NO_MESSAGE = Truncation(threshold=0, severity=Severity.REMOVE)
# Transient error/retry message (tracebacks can be huge): LIGHT-capped from the
# freshest turn so it can never carry a full traceback into context, then dropped
# entirely once it ages past ERROR_RETRY_DISTANCE and is no longer being acted on.
ERROR_RETRY = [
    Truncation(threshold=0, severity=Severity.LIGHT),
    Truncation(threshold=ERROR_RETRY_DISTANCE, severity=Severity.REMOVE),
]

# Graded policy for bulky-but-disposable output (e.g. CLI command output): keep it
# LIGHT-capped while fresh, stub it once a few turns old, drop it once stale.
# Note! This often seems to make the LLM confused...
GRADED = [
    Truncation(threshold=0, severity=Severity.LIGHT),
    Truncation(threshold=GRADED_STUB_DISTANCE, severity=Severity.STUB),
    Truncation(threshold=GRADED_REMOVE_DISTANCE, severity=Severity.REMOVE),
]
