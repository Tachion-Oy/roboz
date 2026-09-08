# Context API migration

The context API now has one class: `roboz.Ctx`. This is a breaking authoring API
change in the Unreleased core and Shed changes. Dependency discovery and resource
identities remain available to deployment registration and health consumers.

## Dependency imports

Dependency identity, discovery, lazy resolution, and live routing belong to
`roboz.dependencies`. Agents, endpoints, and tools consume the same primitives.
Import dependency types and `dedupe_external_dependencies` from that module;
replace imports from `roboz.tooling.dependencies` or dependency imports from
`roboz.tooling`. The removed tooling paths have no forwarding aliases. The
existing top-level `roboz` authoring exports remain available.

`roboz.tooling` owns `Ctx`, `Tool`, and `Factory`. Dependency registration and
binding also belong to `roboz.dependencies`: import `DependencyChecker`,
`DependencyRegistration`, `BoundDependency`, `DependencyContractError`, and
`bind_dependencies` there instead of `roboshed.dependencies.contract`.

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


The former `PromptUserCtx`, `MessageCtx`, `PromptAgentCtx`, `SubagentCtx`,
`BackgroundAgentCtx`, `SnapshotConversationsCtx`, `ConsolidateMemoryCtx`,
`PurgeFilesCtx`, and `SleepBetweenRunsCtx` are removed. The module
`roboz.tools.memory_contexts` is also removed.

### Shed

| Tool or stage | Required context fields | Defaults |
| --- | --- | --- |
| `snapshot_conversations` | `endpoint`, `conversation_root`, `snapshot_root`, `memory_root`, `agent_names`, `token_growth_threshold`, `max_chars` | `max_chars_tolerance_percent=15.0`, `timeout_s=None`, `pipe=None` |
| `consolidate_memory` | `endpoint`, `snapshot_root`, `memory_root`, `conversation_root`, `agent_names`, `min_pending_snapshots`, `max_pending_age_seconds`, `max_chars` | `max_chars_tolerance_percent=15.0`, `timeout_s=None`, `pipe=None` |
| `purge_files` | `pattern`, `max_files`, `folders` | `prune_empty_directories=False` |
| `sleep_between_runs` | `seconds` | `is_cancelled=None`, `conversation_root=None`, fresh empty `agent_names` set |
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
`roboshed.tools.compactification.compactify_messages`. These state classes are
ordinary mutable state, not required context subclasses. `Tool.copy()` retains
the already-bound callable and state; a copy does not allocate new default state.

## Deployment and health consumers

`ExternalDependency` retains `dependency_id`, `kind`, `redacted_metadata()`, and
`materialize()`. `Tool.external_dependencies` and `Agent.external_dependencies()`
continue returning resource objects deduplicated by ID. Contexts now also
implement `ExternalDependencySource` and expose `ctx.external_dependencies()`,
including before binding a tool. No registry or health response format changes
are required by this migration.

```python
ctx = rz.Ctx(
    prefix="[agent]",
    converter=rz.ExecutableDependency("my-converter"),
    endpoint=endpoint,
)
dependencies = ctx.external_dependencies()
```

The return type is `tuple[ExternalDependency, ...]`. Direct resources precede
live-source resources, with the first object retained for each dependency ID.
The method name `external_dependencies` is reserved: rename any configuration
field using that name. Fields named `dependencies` and `values` remain supported.

Contexts recognize direct resources and direct tuple entries, plus live
`ExternalDependencySource` values, including nested contexts, agents, and
catalogs. Factories retain their bound context as a live source; inspection
recomputes source results through contexts, tools, copied tools, and agents.
Subagent graphs remain live after binding; configured but unloaded skills remain
included by default. Collection does not inspect arbitrary nested objects, run
checkers, or materialize resources.

Keep application-level exact registration matching, safe health probes, timeout
policy, and cached health endpoints in the application. Custom endpoint routes
retain their own `materialize()` behavior, including selection changes after
binding. Mock endpoints remain excluded from external dependency discovery.


## Replaceable endpoint references

Keep `LazyExternalDependency` for one declared resource with deferred, cached
construction. Its constructor and ID/kind validation are unchanged; failed
resolutions are not cached. It now also implements `ExternalDependencyReference`
and discovers itself without materializing.

For a route that can select different model IDs, supply a getter to
`DependencyRoute`. The selected resource retains its identity and client cache:

```python
import roboz as rz
from roboz.llm import with_request_options

route = rz.DependencyRoute(lambda: selected_model)
configured = with_request_options(route, extra_body={"reasoning": {"effort": "low"}})
ctx = rz.Ctx(endpoint=configured)
```

A reference has no separate resource ID, kind, or metadata. Discovery delegates
to the current selection without constructing a client or checking health.
Both `with_request_options` and `with_openrouter_policy` retain their concrete
and lazy return types. When given a reference, they return a reference that
selects and applies copied options on every call, retaining the selected client.
Switching first → second → first therefore reuses the first lazy model's client.
Calls already in flight retain their resolved endpoint. Repeated `Agent.invoke()`
calls resolve the current selection again for run lifecycle metadata. `Ctx` bindings remain
immutable; selection state belongs to the object referenced by the getter.

Keep every selectable endpoint in the deployment health catalog, including
unselected models. A route's live discovery is not the complete deployment
catalog. Registration matching and health scheduling remain application policy.
