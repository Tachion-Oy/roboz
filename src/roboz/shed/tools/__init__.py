"""Basic tools; service integrations are imported explicitly."""

from .apply_patch import get_apply_patch
from .contexts import (
    CompactionContext,
    CompactionState,
    ConsolidateMemoryContext,
    FileCommandExecutionContext,
    GuardContext,
    PurgeFilesContext,
    SleepBetweenRunsContext,
    SnapshotConversationsContext,
    StopWhenWatchedAgentsInactiveContext,
)
from .runner import ExecutableCommandCatalog
from .cli_commands import get_run_file_command
from .compactification import get_compactify_messages_when_needed_tool
from .consolidate_memory import consolidate_memory
from .purge_files import purge_files, purge_files_by_threshold
from .sleep_between_runs import sleep_between_runs
from .stop_when_watched_agents_inactive import stop_when_watched_agents_inactive
from .snapshot_conversations import (
    SnapshotMode,
    snapshot_conversations,
)

__all__ = [
    "GuardContext",
    "FileCommandExecutionContext",
    "ExecutableCommandCatalog",
    "CompactionContext",
    "CompactionState",
    "ConsolidateMemoryContext",
    "SnapshotConversationsContext",
    "PurgeFilesContext",
    "SleepBetweenRunsContext",
    "StopWhenWatchedAgentsInactiveContext",
    "SnapshotMode",
    "consolidate_memory",
    "get_apply_patch",
    "get_compactify_messages_when_needed_tool",
    "get_run_file_command",
    "purge_files",
    "purge_files_by_threshold",
    "sleep_between_runs",
    "stop_when_watched_agents_inactive",
    "snapshot_conversations",
]
