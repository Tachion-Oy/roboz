"""Validated filesystem identifiers used by built-in tools."""

from typing import Final

PROMPT_USER_TOOL_NAME: Final[str] = "prompt_user"
PROMPT_USER_AT_START_TOOL_NAME: Final[str] = "prompt_user_at_start"
MESSAGE_USER_TOOL_NAME: Final[str] = "message_user"
STOP_TOOL_NAME: Final[str] = "stop"
SNAPSHOT_CONVERSATIONS_TOOL_NAME: Final[str] = "snapshot_conversations"
CONSOLIDATE_MEMORY_TOOL_NAME: Final[str] = "consolidate_memory"
PURGE_FILES_TOOL_NAME: Final[str] = "purge_files"
PURGE_LOGS_TOOL_NAME: Final[str] = "purge_logs"
PURGE_SNAPSHOTS_TOOL_NAME: Final[str] = "purge_snapshots"
PURGE_MEMORY_TOOL_NAME: Final[str] = "purge_memory"
SLEEP_BETWEEN_RUNS_TOOL_NAME: Final[str] = "sleep_between_runs"
LIBRARIAN_AGENT_NAME: Final[str] = "librarian"
