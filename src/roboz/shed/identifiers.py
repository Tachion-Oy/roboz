"""Names shared by Shed tools and skills."""

from typing import Final

RUN_FILE_COMMAND_TOOL_NAME: Final[str] = "run_file_command"
CONTINUE_FILE_COMMAND_TOOL_NAME: Final[str] = "continue_file_command"
APPLY_PATCH_TOOL_NAME: Final[str] = "apply_patch"
FILESYSTEM_SKILL_NAME: Final[str] = "filesystem"

COMPACTIFY_MESSAGES_TOOL_NAME: Final[str] = "compactify_messages_when_needed"

SNAPSHOT_CONVERSATIONS_TOOL_NAME: Final[str] = "snapshot_conversations"
CONSOLIDATE_MEMORY_TOOL_NAME: Final[str] = "consolidate_memory"
PURGE_FILES_TOOL_NAME: Final[str] = "purge_files"
PURGE_LOGS_TOOL_NAME: Final[str] = "purge_logs"
PURGE_SNAPSHOTS_TOOL_NAME: Final[str] = "purge_snapshots"
PURGE_MEMORY_TOOL_NAME: Final[str] = "purge_memory"
STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME: Final[str] = (
    "stop_when_watched_agents_inactive"
)
SLEEP_BETWEEN_RUNS_TOOL_NAME: Final[str] = "sleep_between_runs"
LIBRARIAN_AGENT_NAME: Final[str] = "librarian"
