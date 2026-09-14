"""Shed factories retain the concrete context and field types callers supply."""

from pathlib import Path
from typing import assert_type

from roboshed.models import ApplyPatch, GuardFilesResult, ParseError, RunFileCommands
from roboshed.tools import (
    CompactionContext,
    ConsolidateMemoryContext,
    FileCommandExecutionContext,
    GuardContext,
    PurgeFilesContext,
    SleepBetweenRunsContext,
    SnapshotConversationsContext,
    consolidate_memory,
    purge_files,
    sleep_between_runs,
    snapshot_conversations,
)
from roboshed.tools.apply_patch import apply_patch, execute_apply_patch_replace
from roboshed.tools.compactification import (
    CompactifyStatus,
    compactify_messages_when_needed,
)
from roboshed.tools.guard import operation_guard
from roboshed.tools.runner import execute_file_command
from roboshed.tools.types import ResolvedFileCommand
from roboz.models import All, Stop, Str
from roboz import Factory, Tool
from roboz.dependencies import ExternalDependency
from roboz.models.truncation import TruncationSpec

assert_type(apply_patch, Factory[ApplyPatch, ResolvedFileCommand | ParseError, Path])
assert_type(execute_apply_patch_replace, Factory[GuardFilesResult, Str, TruncationSpec])
assert_type(
    operation_guard, Factory[ResolvedFileCommand, GuardFilesResult, GuardContext]
)
assert_type(
    execute_file_command,
    Factory[GuardFilesResult, Str | RunFileCommands, FileCommandExecutionContext],
)
assert_type(
    compactify_messages_when_needed, Factory[All, CompactifyStatus, CompactionContext]
)
assert_type(snapshot_conversations, Factory[All, Str, SnapshotConversationsContext])
assert_type(consolidate_memory, Factory[All, Str, ConsolidateMemoryContext])
assert_type(purge_files, Factory[All, Str, PurgeFilesContext])
assert_type(sleep_between_runs, Factory[All, Str | Stop, SleepBetweenRunsContext])


def bind(context: FileCommandExecutionContext) -> None:
    assert_type(
        context.commands.external_dependencies(), tuple[ExternalDependency, ...]
    )
    assert_type(
        execute_file_command(context), Tool[GuardFilesResult, Str | RunFileCommands]
    )
