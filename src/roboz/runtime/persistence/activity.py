"""Durable filesystem markers for active agent conversations."""

from __future__ import annotations

import json
import os
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

_LIFECYCLE_DIR = Path(".lifecycle")
_ACTIVE_RUNS_DIR = _LIFECYCLE_DIR / "active"
_RUN_MARKER_SUFFIX = ".run"


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace ``path`` with a JSON object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def active_marker_paths(
    conversation_root: Path, agent_names: Collection[str]
) -> tuple[Path, ...]:
    """Return active marker paths without reading conversation documents."""
    root = conversation_root.resolve()
    return tuple(
        marker
        for name in set(agent_names)
        if (agent_dir := root / name).resolve().parent == root
        for marker in (agent_dir / _ACTIVE_RUNS_DIR).glob(f"*{_RUN_MARKER_SUFFIX}")
    )


def active_agent_names(
    conversation_root: Path, agent_names: Collection[str]
) -> set[str]:
    """Return agents that have at least one active marker."""
    return {
        marker.parents[2].name
        for marker in active_marker_paths(conversation_root, agent_names)
    }


def mark_conversation_active(*, agent_dir: Path, conversation_id: str) -> Path:
    marker = agent_dir / _ACTIVE_RUNS_DIR / f"{conversation_id}{_RUN_MARKER_SUFFIX}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=False)
    return marker


def clear_conversation_active(*, agent_dir: Path, conversation_id: str) -> None:
    (agent_dir / _ACTIVE_RUNS_DIR / f"{conversation_id}{_RUN_MARKER_SUFFIX}").unlink(
        missing_ok=True
    )


def clear_active_markers(conversation_root: Path) -> int:
    """Remove markers left by agents from a previous process."""
    root = conversation_root.resolve()
    if not root.is_dir():
        return 0
    markers = tuple(
        root.glob(f"*/{_ACTIVE_RUNS_DIR.as_posix()}/*{_RUN_MARKER_SUFFIX}")
    )
    for marker in markers:
        marker.unlink(missing_ok=True)
    return len(markers)


__all__ = [
    "active_agent_names",
    "active_marker_paths",
    "atomic_write_json",
    "clear_active_markers",
    "clear_conversation_active",
    "mark_conversation_active",
]
