# Agent definitions, factories, and ownership

Core describes and constructs agents. Shed supplies concrete agents and deployment
profiles; applications configure and host them.

| Module | Owns |
| --- | --- |
| `roboz.deployment` | `AgentDefinition`, `AgentCapability`, `Capability`, `SubAgentSpec` |
| `roboshed.agents` | `orchestrator()` and `librarian()`, both returning `AgentDefinition` |
| `roboshed.capabilities` | Reusable file, compaction, and maintenance capabilities, alongside `tools` and `skills` |
| `roboshed.deployments.robosprawl` | Project composition, defined directly in the package `__init__.py` |
| Other Shed modules | Sandbox structure and policy, memory, and file tools |
| Application | Configuration, model selection, permission policy, UI conventions, startup and shutdown |

Other deployment profiles can live alongside `robosprawl` in `roboshed.deployments`.
Core imports none of these application modules.

## Capabilities and skills

Both `AgentDefinition` and preset callers configure `capabilities` only. A capability is a configured feature
such as file editing or compaction. It assembles the runtime tools and instructions
needed to provide that feature. Applications choose capabilities; they do not pass
parallel tool and skill lists into `orchestrator()` or `librarian()`.

A `Skill` is a lower-level package of instructions and optional tools. Capabilities
can supply skills for on-demand loading or session-start loading, expose tools
directly, or install automatic tools. These are implementation choices inside a
capability, represented by `Capability`; they are not competing definition or
preset inputs.
For example, `FileEditing` bundles a guarded patch tool with its editing instructions.
`Compactification` supplies an automatic tool without a skill.

The orchestrator always supplies `stop`. `Agent` supplies user
interaction independently of selected capabilities. Only the lower-level `Agent`
and concrete `Capability` expose tool and skill fields; `AgentDefinition` has a
single capability list.

## Generic construction

```python
from roboz import stop
from roboz.deployment import AgentDefinition, Capability
from roboz.llm import MockLLMEndpoint

worker = AgentDefinition(
    name="worker",
    system_prompt="Complete the task and stop.",
    capabilities=(Capability(tools=(stop,)),),
    agent_endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "finished", "value": "done"},
    ]),
)
agent = worker.build()
result, messages = agent.invoke()
```

This works with only `roboz` installed. A definition needs no project, sandbox,
memory folder, provider companion, or host. Without supplied sinks it selects no
persistence or event output. `initial_messages` are explicit caller inputs.

A capability implements `build(pipe, *, default_endpoint) -> Capability`. Its
four ordered contributions are `tools`, `default_tools`, `skills`, and
`auto_loaded_skills`: selectable actions, automatic per-turn work, on-demand
instructions/tools, and session-loaded instructions/tools. Already-bound
inputs are supplied through `Capability` objects in the same list. Capabilities
contribute in list order. Chains/default tools retain shared instance identity;
contributions are not copied.

Each `.build()` creates fresh agent pipes and resolves capabilities against their
owning pipe. `Capability(...)` is itself an `AgentCapability`: its `build()` returns
its already-bound inputs unchanged. Runtime-bound implementations such as
`FileEditing` construct fresh tools and return a `Capability`. Use
`lambda: pipe.cancelled` for cancellation callbacks. Supplied endpoints
and already-bound capability tools remain caller-owned; recreate response-consuming mocks per run.

`SubAgentSpec(definition, tool_name, tool_description)` nests specialists. Builds
reject duplicate recursive agent names before allocating sinks or capabilities.
The `event_sinks` build argument follows the root and specialists. An optional
`event_sink_factory(name)` supplies fresh, agent-specific sinks independently;
parent-specific sinks never leak to children. Core does not choose log locations.
To add root-only automatic work, append a `Capability(default_tools=(... ,))`
to the root definition's capability tuple. The deployment uses this same path to
wire background start tools; no separate tool-injection build argument exists.

## Tool endpoints

Configure tool-specific endpoints on the capability itself. Given configured
`main_model` and `compaction_model` endpoints:

```python
from roboshed.agents import orchestrator
from roboshed.capabilities import Compactification

worker = orchestrator(
    agent_endpoint=main_model,
    capabilities=(Compactification(endpoint=compaction_model),),
)
```

`Compactification()` uses the owning agent's model. An explicit `endpoint` uses
that model instead. Inside the capability's `build()` the choice is direct:

```python
endpoint = self.endpoint if self.endpoint is not None else default_endpoint
if endpoint is None:
    raise ValueError("compaction requires an endpoint")
```

The chosen endpoint goes straight into the tool constructor. The capability
also supplies the owning pipe as a separate runtime input. `AgentDefinition`
only provides `default_endpoint=self.agent_endpoint`; it does not select the
models for individual tools or construct endpoints.

`ConversationSnapshots(endpoint=...)` and `MemoryConsolidation(endpoint=...)`
independently select their models. Each falls back to the owning agent's
`agent_endpoint` when unset; missing required endpoints fail during build.
The Librarian's `is_agentic=False` retains the deterministic maintenance loop
even when a default endpoint is present.

Each build creates fresh pipes and tools while retaining supplied endpoint
objects. Model calls handle cancellation and events using the pipe for that
invocation. Lazy references remain deferred and live references keep their
current selection. Enumerate the actual bound resources with
`agent.external_dependencies()`; no separate capability endpoint registry is
needed. Already-bound `Capability(...)` inputs are returned unchanged.

## The RoboSprawl deployment profile

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboshed.capabilities import FileCommands, FileEditing
from roboshed.deployments.robosprawl import RoboSprawl
from roboshed.sandbox import Sandbox

sandbox = Sandbox(Path("./data"))
deployment = RoboSprawl(
    capabilities=(FileCommands, FileEditing),
    memory_endpoint=MockLLMEndpoint([]),
)
factory = deployment(sandbox, "example", orchestrator_endpoint=MockLLMEndpoint([]))
agent, background_agents = factory.build()
```

`RoboSprawl` selects the persistent orchestrator, derives project locations, and
composes Librarian snapshotting, consolidation, retention, and 120-second cadence
in that order. Watched names include nested specialists. Consumers select a
sequence of configured capabilities or permission factories, a memory endpoint, optional specialists,
and their interaction mode. The `project_context` template supplies the actual
paths derived from the sandbox and project slug at construction time. Select the `robosprawl` skill for static orientation and HUD
guidance; the skill does not embed a deployment’s paths. Permission factories run for every recipe invocation, binding fresh capabilities
to the current project. Override `librarian_capabilities` with a callable accepting
`(sandbox, project_slug, names)` and returning the desired capability sequence. It runs once
per recipe invocation; `names` is the frozen set of recursive foreground agent
names. The returned order is preserved. Supply at least one capability that
provides a default tool: the Librarian runs a non-agentic maintenance pipeline.

The recipe also works directly with `DeploymentFactory` for repeated host runs.
This example constructs the graph without running it. Configure endpoints or
scripted responses before calling `agent.invoke()`. Construction creates no
folders, materializes no providers, and starts no threads.

For custom root or maintenance behavior, `AgenticFactory` accepts ordinary
`AgentDefinition` objects. `orchestrator()` supplies the persistent collaboration
prompt and stop tool. `librarian()` accepts caller-selected capabilities in
execution order. Individual maintenance capabilities remain independently usable;
put `MaintenanceCadence` last and derive watched names from `root.agent_names()`.

The orchestrator stays available across tasks and stops when the user asks,
including standing instructions. It selects no memory location. Use a plain
definition with a task-specific prompt for task-oriented behavior.

`AgenticFactory` binds the sandbox, project slug, and definitions. It seeds root
initial context from `sandbox.project_memory_dir(project_slug)` by default;
`seed_initial_messages_from_memory=False` disables this. It constructs fresh log
sinks below `sandbox.project_logs_dir(project_slug)`.
`include_cli_output` defaults to false. Builds do not mutate either definition.
Foreground and background trees must have disjoint agent names. The Librarian
persists separately; caller foreground sinks do not follow it.

## Invocation and threading

The factory returns `RoboSprawlBundle`, a named tuple defined in the deployment
package. Its fields identify the runnable root and background agents:

```python
bundle = factory.build()
result, messages = bundle.agent.invoke()
background_agents = bundle.background_agents
```

Tuple unpacking also works: `agent, background_agents = factory.build()`.
The named tuple carries references only; it has no forwarding or lifecycle
methods. The host retains `background_agents` for cancellation and shutdown.

`Agent.invoke()` runs synchronously on the calling thread. The deployment wires
`run_background_agent` into the root's default tools; that tool starts and tracks
the Librarian's daemon thread. Subsequent calls reuse a live background thread.
Specialist delegation is synchronous on the root's thread.

Sprawl runs the root in its own worker thread. Its run control retains background
pipes and observes background thread lifecycle events, preserving cancellation,
startup races, and shutdown handling. A CLI can invoke the root on its main thread.

## Sandbox and capability inputs

The sandbox defines its tier and persistence paths once. Its path methods derive
project logs, snapshots, memory, and additional artifact locations from a slug.
Persistence paths must not overlap. Relative paths must remain inside the project,
including after symlink resolution; external storage requires an explicit absolute
path. No dynamic configuration keys become Python attributes.

Applications select and configure `roboshed.capabilities`. RoboSprawl assembles
its capability tuple; any future user-facing selection belongs in Sprawl.
Capabilities do not depend on a named deployment profile.

File capabilities take a concrete `PermissionPolicy`; obtain the standard policy
from `sandbox.permissions(project_slug)` before creating the definition.
`Compactification` uses its owning
endpoint unless given another, and shares its pipe. Its default threshold is
80%; Sprawl explicitly chooses 60%.

## Migration

- Replace the old public layout/project values with one `Sandbox` from
  `roboshed.sandbox`. Configure
  its tier and persistence folder names once, call `sandbox.permissions(slug)`
  for tools, and use its `project_*_dir(slug)` methods for persistence. Project
  identity and lifecycle objects belong to the consuming application.
- Relative project persistence paths may no longer escape the project through
  `..` or symlinks. Use an explicit absolute path for external logs, snapshots,
  or memory storage.

- The optional distribution and namespace are `roboshed`; `roboz[shed]` installs it.
- Import generic definitions and capability contracts from `roboz.deployment`,
  agent presets from `roboshed.agents`, reusable capabilities from
  `roboshed.capabilities`, and the project deployment from
  `roboshed.deployments.robosprawl`.
- Move all direct `AgentDefinition` tool/skill fields into
  `capabilities=(Capability(tools=(...), default_tools=(...), skills=(...),
  auto_loaded_skills=(...)), ...)`. Already-bound and runtime-bound capabilities
  use the same protocol; no compatibility result class is retained.
- Call `AgentDefinition.build()` without a project. Supply persistence explicitly
  through `event_sink_factory` and initial context through `initial_messages`.
- Pass `sandbox=...` and `project_slug=...` into `AgenticFactory`, then unpack
  `agent, background_agents = factory.build(event_sinks=...)`. Call `agent.invoke()`.
- Replace capability `build(pipe, agent_endpoint)` with
  `build(pipe, *, default_endpoint: EndpointLike | None)`. Store tool-specific
  endpoint overrides as ordinary capability fields. Select each explicit value
  or the supplied default in `build()`, validate it, and pass it directly to
  its tool constructor.
- Endpoint inputs use `EndpointLike` from `roboz.llm`: configured endpoints or
  lazy/live references. Endpoint creation receives no runtime controls.
- Replace `LibrarianDefinition`/`LibrarianConstructor` with
  `librarian(capabilities=(...), agent_endpoint=...)`, returning `AgentDefinition`.
  Configure project and watched names on the selected maintenance capabilities.
  Replace `LibrarianTuning` with settings on `ConversationSnapshots`,
  `MemoryConsolidation`, `ArtifactRetention`, and `MaintenanceCadence`.
  Snapshot/consolidation size limits are each named `max_chars`, timeouts
  `timeout_s`, and cadence uses `seconds`. Other tuning fields retain their names
  on the capability that consumes them.
- Move `snapshot_endpoint` to `ConversationSnapshots(endpoint=...)` and
  `consolidation_endpoint` to `MemoryConsolidation(endpoint=...)`; keep
  `agent_endpoint` for a shared default. `endpoint_factory` and its aliases are
  removed. Cancellation remains in the runtime and maintenance tools.
  Standalone builds select their own event sinks; project deployment supplies
  them automatically.
- Memory/summarization tools live in `roboshed.tools`, including snapshotting,
  consolidation, retention, and sleep-between-runs.
- Preset extensions use `capabilities` only. Move `tools`, `default_tools`,
  `skills`, and `auto_loaded_skills` from orchestrator calls into a capability
  returning `Capability`.
- `roboshed.assistant`, `roboshed.demo`, and the `roboz-demo` command are removed.
  Compose task-oriented file agents with `AgentDefinition`, `FileCommands`,
  `FileEditing`, and a stop capability. Supply persistence through build sinks.
- Email input models live in `roboshed.tools.email.inputs` and remain exported
  from `roboshed.tools.email`; the top-level `roboshed.email_inputs` is removed.

No compatibility constructors, import shims, or generic `AgentBundle` wrapper are provided.
Conversation, snapshot, memory, and HTTP formats remain unchanged. This is an
unreleased breaking API change; versions and publication are separate work.


## Repeated construction for any host

`DeploymentFactory` evaluates a `DeploymentRecipe` once per call, then builds the
returned `AgenticFactory`. The recipe receives a sandbox, project slug, and a live orchestrator
endpoint reference. It controls capability choices, prompts, interaction mode,
and dependency allocation. A CLI and a web server can consume the same factory:

```python
from roboshed.agents import orchestrator
from roboshed.deployments.robosprawl import AgenticFactory, DeploymentFactory
from roboz.runtime import Output


def recipe(sandbox, project_slug, *, orchestrator_endpoint):
    return AgenticFactory(
        sandbox=sandbox,
        project_slug=project_slug,
        orchestrator=orchestrator(
            agent_endpoint=orchestrator_endpoint,
            interaction_mode=Output.CLI,
        ),
    )


factory = DeploymentFactory(recipe)
bundle = factory(
    sandbox,
    "example",
    endpoint_getter=lambda: selected_endpoint,
    event_sinks=(),
)
bundle.agent.invoke()
```

The shared `RunFactory` protocol is the single host construction contract:
`(sandbox, project_slug, *, endpoint_getter, event_sinks) -> RoboSprawlBundle`. Constructing a
bundle never invokes its agents, starts threads, or creates persistence files.
Hosts retain the returned background agents and own invocation and shutdown.

The recipe is evaluated anew for each run. Allocate mutable scripted endpoints
and other run-owned inputs inside it. Captured lazy production endpoints remain
shared deliberately; their targets retain responsibility for materialization and
caching. The factory neither deep-copies supplied objects nor closes borrowed
clients. An optional `event_sink_factory` allocates extra sinks once per build;
these precede supplied host sinks. Capabilities receive the constructed pipe, so
runtime controls can be bound before invocation without post-build patching.

`roboz.DependencyRoute(getter)` delegates materialization and discovery to the
current target. It accepts concrete dependencies and dependency references and
never has a separate dependency identity. Root inference and capabilities using
the default endpoint follow the same route. Independently supplied memory
endpoints retain their own selection.

`roboz.llm.ModelSelector` (defined in `roboz.llm.endpoints`) accepts a labeled
catalog and default
lazy endpoint. Selection validates identities under a lock without constructing
clients. Consumers decide what the catalog contains and whether changes apply to
new runs or a particular existing run.

## Dependency inspection and health

Use `inspect_dependencies` from the shared deployment profile to build against
a temporary project with the configured folder names, collect foreground and
background dependencies, and bind exact registrations. Supply selectable but
currently unused models through `additional_dependencies`. Temporary inspection
storage is removed on success and failure.

`roboz.dependencies` owns `DependencyRegistration`, `BoundDependency`,
`DependencyContractError`, and `bind_dependencies` for exact ID/kind
binding. Explicit registrations must equal the discovered graph; duplicate,
missing, extra, or mismatched registrations are rejected before checks run.
When registrations are omitted, only executable and model endpoint checks are
inferred; custom kinds require explicit registrations.

`roboshed.dependency_health` provides the monitor, sanitized records and reason
codes, and executable, OpenAI-compatible discovery, and service-owned protocol
probes. These inspect protocols without importing provider SDKs. The monitor
bounds concurrency and prevents overlapping checks after observation timeouts.
An observation timeout leaves an active checker holding its concurrency slot
until it finishes; probes should bound their own I/O. Cancelling a thread-backed
check cannot terminate its worker. Unexpected observation failures are reported
with sanitized diagnostics and retried on the next interval. Calling `start()`
also restarts a scheduler task that has exited.
Consumers own scheduler start/stop and readiness decisions; an unavailable
resource does not automatically make an application unready.

Project-scoped tools can use `FileCommands(sandbox.permissions(project_slug))`
and `FileEditing(sandbox.permissions(project_slug))`. The shared policy allows sandbox reads
and writes in the current project, asks before shared-area writes, and denies
other writes or paths outside the sandbox. Policy construction has no filesystem
side effects; hosts still own directory preparation and tool invocation.
Project slugs and configured sandbox folder names are literal paths: wildcard
characters in their names do not expand the derived permission rules.
