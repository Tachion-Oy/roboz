# Concrete contexts for Shed tools

Shed's file-command, guard, editing, compaction, snapshot, consolidation, retention,
cadence, and email factories now bind concrete typed objects. All these context classes
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
| `search_email`, `read_email`, and email execution stages | `EmailContext` |
| `resolve_email_input`, `resolve_reply_draft_input`, `resolve_attachment_download` | `pathlib.Path` directly |

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

## Email services and contexts

`EmailService` inherits `ExternalDependency` and declares the complete mailbox
operation interface. Implement its stable `dependency_id`, safe
`redacted_metadata()`, read-only `probe()`, and draft/search/read/download/reply
methods. Incomplete subclasses cannot instantiate. The service supplies the
network-service category and `check() -> bool`: a successful probe returns a
dictionary, while unavailable or failed access raises an exception. The check
returns `True` after that successful probe; an invalid probe result raises
`TypeError`. Provider-specific probes own authentication and service access.

`get_work_with_email` keeps its existing arguments and constructs `EmailContext`
internally. It requires a complete `EmailService`. For direct bindings, import
`EmailContext` from `roboshed.tools.contexts`, `roboshed.tools`, or
`roboshed.tools.email`:

```python
from roboshed.tools.contexts import EmailContext
from roboshed.tools.email import EmailService
from roboshed.tools.email.messages import search_email
from roboz import Tool
from roboz.models import Str
from roboshed.tools.email.inputs import SearchEmail


def bind_search(service: EmailService) -> Tool[SearchEmail, Str]:
    context = EmailContext(
        service=service,
        is_cancelled=lambda: False,
        timeout_s=30.0,
        pipe=None,
    )
    return search_email(context)
```

The context reports the supplied service's resources without probing it. Copies
retain that same context and service. Attachment resolvers and permission guards
report no service resources; execution stages, search, and read tools report the
service. Agent inspection deduplicates those reports. The service can also be
passed directly to `DependencyHealthMonitor((service,))` without any agent.

Inbox confirmation remains disabled by default, and existing draft-only behavior,
attachment permissions, input normalization, cancellation, timeouts, and sanitized
error messages are preserved. Checks run only when requested through `check()` or
health observation, independently of normal mailbox operations. `_prepare_email_context`
and the email `_prepare_ctx` hooks are removed.

## Checkpoint boundary

Shed capability builders now construct these central context classes. Public
capability arguments, owner configuration, model overrides, tool order, and defaults
are preserved. Each build creates fresh contexts and runtime state; tools retain
the supplied endpoints. Building and inspecting those tools does not initialize
clients. The existing deployment methods and application configuration/build/invoke
sequence are unchanged.

The RoboSprawl recipe uses `LLMEndpointRoute` for its selectable orchestrator
model and keeps the Librarian's endpoint fixed. Legacy core and Shed consumers
are migrated. Proton implementation work remains deferred.
Deployment dependency discovery uses the existing build path and requires
configuration sufficient for construction; agents do not need to be running.
The integrated core, Endpoints, and Shed release gates cover this contract.
