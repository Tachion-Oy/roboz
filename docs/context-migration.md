# Context API migration

The context API now has one class: `roboz.Ctx`. This is a breaking authoring API
change in the Unreleased core and Shed changes. Dependency discovery and resource
identities remain available to deployment registration and health consumers.

## Replace context classes and resource wrappers

| Previous API | Replacement |
| --- | --- |
| `FactoryCtx` and a custom dataclass subclass | `Ctx(**values)`; annotate the callable's context as `Ctx` |
| Built-in specialized context constructors | `Ctx` with the same field names, using keyword arguments |
| `ToolDependency(resource)` | `resource` |
| `ctx.service.resource.method(...)` | `ctx.service.method(...)` |
| `ctx.converter.resource.require()` | `ctx.converter.require()` |
| `EndpointBinding` | `EndpointLike` |
| `bind_endpoint(endpoint)` | `endpoint` |
| `endpoint_resource(ctx.endpoint)` | `ctx.endpoint` |
| Entries in `Tool.dependencies` | Direct `ExternalDependency` objects; remove `.resource` access |

These removed names have no compatibility aliases. Remove imports of
`FactoryCtx`, specialized context classes, `ToolDependency`, `EndpointBinding`,
`bind_endpoint`, and `endpoint_resource`.

```python
import roboz as rz

@rz.factory
def add_prefix(
    input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx
) -> rz.Str:
    """Prefix the supplied text with the configured label."""
    return rz.Str(value=f"{ctx.prefix}{input.value}")

tool = add_prefix(rz.Ctx(prefix="[agent] "))
```

Required/default field annotations from custom dataclasses do not transfer
automatically. Supply custom defaults when constructing `Ctx` and retain any
application-specific validation in your constructor or tool code. Arbitrary
context field names/types are dynamic; input/output and chain typing remain
enforced. Missing context attributes raise `AttributeError`.

`Ctx` is not a dataclass and has no positional constructor arguments. For code
that used `dataclasses.replace`, retain a configuration dictionary and construct
another context with `Ctx(**(values | overrides))`.

## Built-in fields and defaults

Existing high-level builders retain their signatures and defaults. Direct
built-in factory binding checks required fields and supplies the defaults below.
Field values retain their previous meanings and types; values are not coerced by
`Ctx`. Explicit fields override defaults, including explicit `None`.

### Core

| Tool | Required context fields | Defaults |
| --- | --- | --- |
| `prompt_user` | `timeout_reply` | None |
| `message_user`, `prompt_user_at_start` | `message` | None |
| `prompt_agent` | `endpoint`, `active_tools`, `pipe` | None |
| `run_subagent` | `agent` | None |
| `run_background_agent` | `agent` | Fresh `state` per binding |
| `snapshot_conversations` | `endpoint`, `conversation_root`, `snapshot_root`, `memory_root`, `agent_names`, `token_growth_threshold`, `max_chars` | `max_chars_tolerance_percent=15.0`, `timeout_s=None`, `pipe=None` |
| `consolidate_memory` | `endpoint`, `snapshot_root`, `memory_root`, `conversation_root`, `agent_names`, `min_pending_snapshots`, `max_pending_age_seconds`, `max_chars` | `max_chars_tolerance_percent=15.0`, `timeout_s=None`, `pipe=None` |
| `purge_files` | `pattern`, `max_files`, `folders` | `prune_empty_directories=False` |
| `sleep_between_runs` | `seconds` | `is_cancelled=None`, `conversation_root=None`, fresh empty `agent_names` set |

The former `PromptUserCtx`, `MessageCtx`, `PromptAgentCtx`, `SubagentCtx`,
`BackgroundAgentCtx`, `SnapshotConversationsCtx`, `ConsolidateMemoryCtx`,
`PurgeFilesCtx`, and `SleepBetweenRunsCtx` are removed. The module
`roboz.tools.memory_contexts` is also removed.

### Shed

| Tool or stage | Required context fields | Defaults |
| --- | --- | --- |
| File-command resolution | `base`, `specs`, `allow_rules`, `deny_rules`, `ask_rules`, `takes_precedence`, `default_verdict` | None |
| Patch and email attachment/path resolution | `base` | None |
| `operation_guard` | `base`, `takes_precedence`, `deny`, `allow`, `ask`, `default_verdict` | `command_specs=()`, `pipe=None` |
| Patch execution | `truncation` | None |
| File-command execution | `truncation`, `commands` | None |
| Email provider execution | `service`, `is_cancelled`, `timeout_s`, `pipe` | `prompt_before_inbox_read=False` |
| `compactify_messages_when_needed` | `endpoint`, `threshold_percent`, `system_prompt`, `skill_message` | `pipe=None`, `timeout_s=None`, fresh `state` per binding |

The former `RunFileCommandsCtx`, `ApplyPatchCtx`, `EmailToolCtx`, `GuardCtx`,
`ExecuteApplyPatchCtx`, `ExecuteFileCommandCtx`, `EmailRuntimeContext`, and
`CompactifyMessagesCtx` are removed. `ExecutableCommandCatalog` stores direct
`ExecutableDependency` objects in `bindings`, and `binding_for()` returns the
executable itself.

## State and resource ownership

Context attributes are immutable; the objects they contain retain their identity
and mutability. Binding does not deep-copy contexts, clients, agents, or resources.
Built-in default completion creates a context captured by the tool without
adding fields to the caller's context.

Omitted background-worker/compaction state is allocated once per binding.
Previously, creating a specialized context allocated its default state; reusing
that context could therefore share it across bindings. To keep intentional
sharing or inspect the state directly, provide it explicitly:

```python
import roboz as rz
from roboz.agent.background_agent import BackgroundAgentState

state = BackgroundAgentState()
background = rz.run_background_agent(rz.Ctx(agent=child, state=state))
```

Compaction similarly accepts `CompactionState` from
`roboz_shed.tools.compactification.compactify_messages`. These state classes are
ordinary mutable state, not required context subclasses. `Tool.copy()` retains
the already-bound callable and state; a copy does not allocate new default state.

## Deployment and health consumers

`ExternalDependency` retains `dependency_id`, `kind`, `redacted_metadata()`, and
`materialize()`. `Tool.external_dependencies` and `Agent.external_dependencies()`
continue returning resource objects deduplicated by ID. No registry or health
response format changes are required by this migration.

Contexts recognize direct resources and direct tuple entries, plus live
`ExternalDependencySource` values. Subagent graphs remain live after binding;
configured but unloaded skills remain included by default. Collection does not
inspect arbitrary nested objects, run checkers, or materialize resources.

Keep application-level exact registration matching, safe health probes, timeout
policy, and cached health endpoints in the application. Custom endpoint routes
retain their own `materialize()` behavior, including selection changes after
binding. Mock endpoints remain excluded from external dependency discovery.
