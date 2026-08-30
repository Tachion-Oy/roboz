from datetime import datetime
from logging import getLogger
from pathlib import Path
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


def delete_agent_context(*, root: Path, agent: str):
    agent_name = Path(agent)
    if (
        agent_name.is_absolute()
        or len(agent_name.parts) != 1
        or agent_name.name in {"", ".", ".."}
    ):
        raise ValueError("agent must name one direct child of root")
    resolved_root = root.resolve()
    agent_path = (resolved_root / agent_name).resolve()
    if agent_path.parent != resolved_root:
        raise ValueError("agent must resolve to a direct child of root")
    if agent_path.is_dir():
        shutil.rmtree(agent_path)
        return
    logger.warning(f"Context path not found or is not a directory: {agent_path}")
