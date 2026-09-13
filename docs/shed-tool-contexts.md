# Concrete contexts for Shed tools

Shed's file-command, guard, editing, compaction, snapshot, consolidation, retention,
and cadence factories now bind concrete typed objects. All these context classes
and `CompactionState` are defined in `roboshed.tools.contexts` and re-exported from
`roboshed.tools`. The ready-made `get_run_file_command`, `get_apply_patch`, and
`get_compactify_messages_when_needed_tool` helpers keep their existing keyword
arguments and construct the appropriate contexts internally.

## Direct factory binding

Replace dynamic `Ctx(...)` arguments to direct factories with the corresponding
concrete context. Required fields are constructor arguments; defaults live on the
context class. Type checking and editor completion now follow those fields.

| Factory | Supplied context |
| --- | --- |
| `operation_guard` | `GuardContext` |
| `resolve_input` | `FileCommandResolverContext` |
| `execute_file_command` | `FileCommandExecutionContext` |
| `apply_patch` | `pathlib.Path` directly |
| `execute_apply_patch_replace` | `TruncationSpec` directly |
| `compactify_messages_when_needed` | `CompactionContext` |
| `snapshot_conversations` | `SnapshotConversationsContext` |
| `consolidate_memory` | `ConsolidateMemoryContext` |
| `purge_files` | `PurgeFilesContext` |
| `sleep_between_runs` | `SleepBetweenRunsContext` |

For example, constructing and inspecting this retention tool does not read or
delete any files:

```python
from pathlib import Path

from roboshed.tools import PurgeFilesContext, purge_files

context = PurgeFilesContext(
    folders=[Path("conversation_logs")],
    pattern="*.json",
    max_files=500,
)
bound = purge_files(context)
assert bound.external_dependencies() == ()
```

The tool's work still happens when invoked. Permission decisions, path checks,
command arguments, prompts, artifact formats, retention thresholds, cancellation,
timeouts, and compaction budgets retain their existing behavior.

## Resource inspection and state

`FileCommandExecutionContext` reports the executable resources held by its
`ExecutableCommandCatalog`. The catalog implements `HasExternalDependencies` and
retains the supplied executable objects. Inspection does not search PATH or run a
command; execution still resolves the selected executable with `require()`.

Compaction, snapshot, and consolidation contexts report their concrete endpoint's
resources through `external_dependencies()`. Binding, copying, and inspection
leave the client untouched. Idle maintenance does not initialize a client merely
because its context holds an endpoint. A real endpoint's deferred client initializes
when the operation reaches a provider request. Mock endpoints report no external
resources. Permission, path, output-limit, retention, and cadence contexts contain
configuration and report no external dependencies through their bound tools.

Binding retains the exact supplied context. Rebinding or copying intentionally
shares its fields and state. `CompactionContext.state` has a fresh `CompactionState`
for each newly constructed context; reusing one context shares its counter.
`get_compactify_messages_when_needed_tool(...)` constructs a fresh context for each
call, preserving independent counters between helper calls. To make another
independent direct binding, construct another context. There are no `_prepare_ctx`
hooks or implicit field/default copies at binding time.

The factory schema still excludes `ctx`; these constructor fields are Python
configuration, not model-supplied input. See the
[primitive contract](dependency-primitives.md) for arbitrary contexts, exact typing,
resource inspection, copying, and deferred client initialization.

## Checkpoint boundary

This checkpoint changes tool contexts only. Existing deployment methods,
capability builders, and recipes are untouched. Migrating the capability call sites
to these context classes remains a subsequent step; this document does not change
when an application configures, builds, or invokes its deployment.

Email contexts/service contracts, capability bindings, recipes/live model selection,
and obsolete consumer examples/tests remain to migrate. Proton remains deferred.
Discovery before full deployment configuration is available remains unresolved.
The complete library and release gates are not ready at this checkpoint.
