"""Versioned coverage metadata embedded in conversation snapshot artifacts."""

from dataclasses import dataclass
from typing import Final

SNAPSHOT_COVERAGE_TAG: Final[str] = "librarian:snapshot-coverage"
SNAPSHOT_COVERAGE_VERSION: Final[int] = 1
SNAPSHOT_COVERAGE_SEQUENCE_FIELD: Final[str] = "sequence"

_MIN_COVERED_SEQUENCE: Final[int] = 0
_SNAPSHOT_COVERAGE_ANY_VERSION_PREFIX: Final[str] = (
    f"<!-- {SNAPSHOT_COVERAGE_TAG} "
)
_SNAPSHOT_COVERAGE_PREFIX: Final[str] = (
    f"{_SNAPSHOT_COVERAGE_ANY_VERSION_PREFIX}v{SNAPSHOT_COVERAGE_VERSION} "
    f"{SNAPSHOT_COVERAGE_SEQUENCE_FIELD}="
)
_SNAPSHOT_COVERAGE_SUFFIX: Final[str] = " -->"


@dataclass(frozen=True)
class SnapshotDocument:
    """Human snapshot content and its optional machine-owned coverage cursor."""

    content: str
    covered_through_sequence: int | None
    coverage_marker_present: bool


def format_snapshot_document(content: str, *, covered_through_sequence: int) -> str:
    """Append a hidden, versioned coverage cursor to human snapshot content."""
    if covered_through_sequence < _MIN_COVERED_SEQUENCE:
        raise ValueError("covered_through_sequence must be non-negative")
    marker = (
        f"{_SNAPSHOT_COVERAGE_PREFIX}{covered_through_sequence}"
        f"{_SNAPSHOT_COVERAGE_SUFFIX}"
    )
    return f"{content.rstrip()}\n\n{marker}\n"


def parse_snapshot_document(text: str) -> SnapshotDocument:
    """Separate a trailing coverage marker without exposing it to an LLM prompt."""
    stripped = text.rstrip()
    content, separator, final_line = stripped.rpartition("\n")
    candidate = final_line.strip()
    if not candidate.startswith(_SNAPSHOT_COVERAGE_ANY_VERSION_PREFIX):
        return SnapshotDocument(
            content=stripped,
            covered_through_sequence=None,
            coverage_marker_present=False,
        )

    human_content = content.rstrip() if separator else ""
    if not (
        candidate.startswith(_SNAPSHOT_COVERAGE_PREFIX)
        and candidate.endswith(_SNAPSHOT_COVERAGE_SUFFIX)
    ):
        return SnapshotDocument(
            content=human_content,
            covered_through_sequence=None,
            coverage_marker_present=True,
        )

    serialized_sequence = candidate[
        len(_SNAPSHOT_COVERAGE_PREFIX) : -len(_SNAPSHOT_COVERAGE_SUFFIX)
    ]
    try:
        covered_through_sequence = int(serialized_sequence)
    except ValueError:
        covered_through_sequence = None
    if (
        covered_through_sequence is not None
        and covered_through_sequence < _MIN_COVERED_SEQUENCE
    ):
        covered_through_sequence = None
    return SnapshotDocument(
        content=human_content,
        covered_through_sequence=covered_through_sequence,
        coverage_marker_present=True,
    )


__all__ = [
    "SNAPSHOT_COVERAGE_SEQUENCE_FIELD",
    "SNAPSHOT_COVERAGE_TAG",
    "SNAPSHOT_COVERAGE_VERSION",
    "SnapshotDocument",
    "format_snapshot_document",
    "parse_snapshot_document",
]
