from datetime import datetime
from logging import getLogger
from pathlib import Path, PureWindowsPath
import shutil

logger = getLogger(__name__)


def get_conversation_run_path(
    data_folder: Path, *, started_at: datetime, conversation_id: str
) -> Path:
    if not isinstance(started_at, datetime):
        raise TypeError("started_at must be datetime")
    return (
        data_folder
        / f"{started_at.year:04d}"
        / f"{started_at.month:02d}"
        / f"{started_at.day:02d}"
        / f"{conversation_id}.json"
    )


def get_file_count(p: Path | None) -> int | None:
    if not p or not p.exists() or not p.is_dir():
        return None
    return len([f for f in p.iterdir() if f.is_file()])


def _direct_child_path(*, root: Path, name: str) -> Path:
    if not isinstance(name, str):
        raise TypeError("child name must be a string")
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
        or PureWindowsPath(name).is_absolute()
    ):
        raise ValueError("child name must be one non-empty path component")

    resolved_root = root.resolve()
    candidate = root / name
    resolved_candidate = candidate.resolve()
    if (
        resolved_candidate == resolved_root
        or resolved_candidate.parent != resolved_root
    ):
        raise ValueError("child path must resolve directly beneath root")
    return candidate


def delete_agent_context(*, root: Path, agent: str):
    agent_path = _direct_child_path(root=root, name=agent)
    if agent_path.is_dir():
        shutil.rmtree(agent_path)
        return
    logger.warning(f"Context path not found or is not a directory: {agent_path}")
