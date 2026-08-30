"""Canonical model-facing identifiers for the lean standard library."""

from typing import Final

RUN_FILE_COMMAND_TOOL_NAME: Final[str] = "run_file_command"
CONTINUE_FILE_COMMANDS_TOOL_NAME: Final[str] = "continue_file_commands"
RUN_SHELL_SCRIPT_TOOL_NAME: Final[str] = "run_shell_script"
APPLY_PATCH_TOOL_NAME: Final[str] = "apply_patch"
PURGE_LOGS_TOOL_NAME: Final[str] = "purge_logs"
PURGE_SNAPSHOTS_TOOL_NAME: Final[str] = "purge_snapshots"
PURGE_MEMORY_TOOL_NAME: Final[str] = "purge_memory"
COMPACTIFY_MESSAGES_TOOL_NAME: Final[str] = "compactify_messages"
SNAPSHOT_CONVERSATIONS_TOOL_NAME: Final[str] = "snapshot_conversations"
CONSOLIDATE_MEMORY_TOOL_NAME: Final[str] = "consolidate_memory"
SLEEP_BETWEEN_RUNS_TOOL_NAME: Final[str] = "sleep_between_runs"

CLI_COMMANDS_SKILL_NAME: Final[str] = "cli_commands"
FILE_EDIT_SKILL_NAME: Final[str] = "file_edit"
