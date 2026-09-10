# Deployable agents and deployments

Core describes and constructs agents. Shed supplies concrete agents and deployment
profiles; applications configure and host them.

| Module | Owns |
| --- | --- |
| `roboz.deployment` | `DeployableAgent`, `Deployment`, `AgentCapability`, `Capability` |
| `roboshed.agents` | `orchestrator()` and `librarian()`, both returning `DeployableAgent` |
| `roboshed.capabilities` | Reusable file, compaction, and maintenance capabilities, alongside `tools` and `skills` |
| `roboshed.deployments.robosprawl` | The `robosprawl(...)` function returning a configured `Deployment` |
| Other Shed modules | Sandbox structure and policy, memory, and file tools |
| Application | Configuration, model selection, permission policy, UI conventions, startup and shutdown |

Other deployment profiles can live alongside `robosprawl` in `roboshed.deployments`.
Core imports none of these application modules.

## Capabilities and skills

Both `DeployableAgent` and preset callers configure `capabilities` only. A capability is a configured feature
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
and concrete `Capability` expose tool and skill fields; `DeployableAgent` has a
single capability list.

## Generic construction

```python
from roboz import stop
from roboz.deployment import DeployableAgent, Capability
from roboz.llm import MockLLMEndpoint

worker = DeployableAgent(
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

Each definition has two recursive slots of the same type:
`subagents: tuple[DeployableAgent, ...]` and
`background_agents: tuple[DeployableAgent, ...]`. Sub-agents become selectable
delegation tools, named and described by the child definition. They run
synchronously. Background agents become default start/heartbeat tools named
`start_background_agent_<name>`; they run in daemon threads when their parent
is invoked. No spec object or invocation flags are needed.

Builds reject duplicate names anywhere in the graph before allocating sinks or
capabilities. `agent_names()` includes all agents;
`agent_names(include_background=False)` excludes entire background branches.
The `event_sinks` build argument follows synchronous children only. An optional
`event_sink_factory(name)` supplies fresh agent-specific sinks independently,
including for background agents and their descendants.

To retain background agents for host control, put the root definition in a
`Deployment`. Given configured `root`, `specialist`, and `maintenance` definitions:

```python
from dataclasses import replace
from roboz.deployment import Deployment

deployment = Deployment(
    root=replace(
        root,
        subagents=(specialist,),
        background_agents=(maintenance,),
    ),
    initial_messages=("Additional startup context.",),
)
agent, background_agents = deployment.build()
result, messages = agent.invoke()
```

`Deployment.build()` returns a plain `tuple[Agent, tuple[Agent, ...]]`. The
background tuple contains every background invocation target, including those
nested below sub-agents or other background agents. Traversal visits sub-agents
first, then background branches, retaining each background before its descendants.
Deployment context precedes the root's existing initial messages without changing
the definition. Construction invokes no agents, starts no threads, writes no
persistence files, and materializes no providers.

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
also supplies the owning pipe as a separate runtime input. `DeployableAgent`
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

## The RoboSprawl deployment instance

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboshed.capabilities import FileCommands, FileEditing
from roboshed.deployments.robosprawl import robosprawl
from roboshed.sandbox import Sandbox

sandbox = Sandbox(Path("./data"))
deployment = robosprawl(
    sandbox, "example",
    orchestrator_endpoint=MockLLMEndpoint([]),
    capabilities=(FileCommands, FileEditing),
    memory_endpoint=MockLLMEndpoint([]),
)
agent, background_agents = deployment.build()
```

`robosprawl(...)` returns an ordinary `Deployment` instance. It binds project
permissions and context, constructs the orchestrator, and adds the Librarian to
the root's `background_agents` tuple. Caller-supplied `subagents` and
`background_agents` are ordinary `DeployableAgent` definitions.

The default maintenance sequence remains snapshots, consolidation, retention,
and a 120-second cadence. Override `librarian_capabilities` with
`(sandbox, project_slug, names) -> Sequence[AgentCapability]`; it runs once per
composition and receives recursive foreground names only. Supply automatic work
and a stopping policy, normally `MaintenanceCadence` last.

Memory is seeded from `sandbox.project_memory_dir(project_slug)`;
`seed_initial_messages_from_memory=False` disables it. Agent-specific sinks
persist below `sandbox.project_logs_dir(project_slug)`.
`include_cli_output` defaults to false. The `project_context` template supplies
actual project paths; the separate `robosprawl` skill supplies static orientation
and HUD guidance.

The orchestrator remains available across tasks until the user asks it to stop,
including standing instructions. Use a plain definition with a task-specific
prompt for task-oriented behavior. This example builds only; supply configured
endpoints or scripted responses before invoking it.

## Invocation and threading

Invoke the returned root directly and retain the background tuple for control.
Use each agent's existing `pipe.cancel()` or `pipe.interrupt()` as appropriate;
the deployment does not add a lifecycle API or propagate signals between agents.

The parent runs its background-start tools automatically in its default
sequence. A first call starts the background agent; subsequent calls return a
heartbeat while its thread is alive, or restart it after it exits. Background
work may continue after the parent finishes. Hosts own cancellation and shutdown,
including lifecycle observation and any waiting for threads to finish.

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

- Rename `AgentDefinition` imports to `DeployableAgent`.
- Remove `SubAgentSpec`: put child definitions directly in `subagents`.
  Delegation tools now use each child's `name` and `description`. Update
  model-facing tool references to the child's existing name; do not rename
  persisted agent identities merely to preserve an old delegation-tool alias.
- Put background definitions directly in `background_agents`. Their start
  tools are always defaults; no background/default flags are accepted.
- Replace the `RoboSprawl` configuration class with one `robosprawl(...)` call
  supplying its sandbox, slug, endpoints, and choices together.
- `DeploymentFactory`, `DeploymentRecipe`, `RunFactory`, and
  `RoboSprawlBundle` are removed. Use ordinary composition functions and tuple
  unpacking instead of `bundle.agent` or `bundle.background_agents`.
- Import `inspect_dependencies` from `roboshed.dependency_health`. Its callback
  now receives only the temporary sandbox and returns a `Deployment`; capture
  the project slug and endpoint choices in that callback.


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
- Move all direct `DeployableAgent` tool/skill fields into
  `capabilities=(Capability(tools=(...), default_tools=(...), skills=(...),
  auto_loaded_skills=(...)), ...)`. Already-bound and runtime-bound capabilities
  use the same protocol; no compatibility result class is retained.
- Call `DeployableAgent.build()` without a project. Supply persistence explicitly
  through `event_sink_factory` and initial context through `initial_messages`.
- Replace `AgenticFactory` with `Deployment(root=...)`. Supply initial context
  and agent-specific sinks explicitly, or use `robosprawl(...)` for project policy.
  Unpack `agent, background_agents = deployment.build(event_sinks=...)`.
- Replace capability `build(pipe, agent_endpoint)` with
  `build(pipe, *, default_endpoint: EndpointLike | None)`. Store tool-specific
  endpoint overrides as ordinary capability fields. Select each explicit value
  or the supplied default in `build()`, validate it, and pass it directly to
  its tool constructor.
- Endpoint inputs use `EndpointLike` from `roboz.llm`: configured endpoints or
  lazy/live references. Endpoint creation receives no runtime controls.
- Replace `LibrarianDefinition`/`LibrarianConstructor` with
  `librarian(capabilities=(...), agent_endpoint=...)`, returning `DeployableAgent`.
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
  Compose task-oriented file agents with `DeployableAgent`, `FileCommands`,
  `FileEditing`, and a stop capability. Supply persistence through build sinks.
- Email input models live in `roboshed.tools.email.inputs` and remain exported
  from `roboshed.tools.email`; the top-level `roboshed.email_inputs` is removed.

No compatibility constructors, import shims, or generic `AgentBundle` wrapper are provided.
Conversation, snapshot, memory, and HTTP formats remain unchanged. This is an
unreleased breaking API change; versions and publication are separate work.


## Repeated construction for any host

Call the composition function for each project/run, then call `build()`.
A function or closure can allocate response-consuming mock endpoints and
run-owned capabilities. Supplied lazy production endpoints remain shared
deliberately; nothing is deep-copied or closed by deployment construction.
Calling `build()` twice on the same definition creates fresh runtime state but
does not reset caller-supplied mocks or already-bound tools.

Pass composed caller sinks directly to `build(event_sinks=...)`. Live model
routing is an ordinary endpoint input such as `DependencyRoute(getter)`; it
does not require a deployment-specific host protocol.

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

Use `inspect_dependencies` from `roboshed.dependency_health` with a
`Callable[[Sandbox], Deployment]`, the configured `sandbox`, and
`registrations`. The callback receives an isolated sandbox with the configured
folder names. It returns a deployment to build without invocation; the root's
existing `external_dependencies()` follows both sub-agent and background tools.
Supply selectable but currently unused models through `additional_dependencies`.
Temporary inspection storage is removed on success and failure.

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
