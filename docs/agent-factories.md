# Agent definitions, factories, and ownership

Core describes and constructs agents. Shed supplies concrete agents and deployment
profiles; applications configure and host them.

| Module | Owns |
| --- | --- |
| `roboz.deployment` | `AgentDefinition`, `AgentCapability`, `Capability`, `SubAgentSpec` |
| `roboshed.agents` | `orchestrator()` and `librarian()`, both returning `AgentDefinition` |
| `roboshed.capabilities` | Reusable file, compaction, and maintenance capabilities, alongside `tools` and `skills` |
| `roboshed.deployments.robosprawl` | Project composition, defined directly in the package `__init__.py` |
| Other Shed modules | Workspace/project structure, permissions, memory and file tools |
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

This works with only `roboz` installed. A definition needs no project, workspace,
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
from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import (
    ArtifactRetention, ConversationSnapshots, FileCommands, FileEditing,
    MaintenanceCadence, MemoryConsolidation,
)
from roboshed.deployments.robosprawl import AgenticFactory
from roboshed.workspace import Project, Workspace, WorkspacePermissions

project = Project(Workspace(Path("./data")), "example")
permissions = WorkspacePermissions.local(project.root)
root = orchestrator(
    agent_endpoint=MockLLMEndpoint([]),
    capabilities=(FileCommands(permissions), FileEditing(permissions)),
)
memory_agent = librarian(
    agent_endpoint=MockLLMEndpoint([]),
    capabilities=(
        ConversationSnapshots(project, root.agent_names()),
        MemoryConsolidation(project, root.agent_names()),
        ArtifactRetention(project),
        MaintenanceCadence(project, root.agent_names()),
    ),
)
factory = AgenticFactory(project=project, orchestrator=root, librarian=memory_agent)
agent, background_agents = factory.build()
```

This example constructs the graph without running it. Configure endpoints or
scripted responses before calling `agent.invoke()`. Construction creates no
folders, materializes no providers, and starts no threads.

Both role inputs are ordinary `AgentDefinition` objects. `orchestrator()` supplies
the persistent collaboration prompt and stop tool. `librarian()` selects a
deterministic loop and accepts caller-selected capabilities in execution order.
The example composes snapshotting, consolidation, retention, and cadence from
`roboshed.capabilities`; each owns its settings and builds its own tools.
Supply automatic work and a stopping policy. `MaintenanceCadence` goes last: it
waits while watched conversations are active and stops when the project is idle.
Capabilities can also be used independently, such as retention with cadence and
no model. Derive the watch set from `root.agent_names()` to include specialists.

The orchestrator stays available across tasks and stops when the user asks,
including standing instructions. It selects no memory location. Use a plain
definition with a task-specific prompt for task-oriented behavior.

`AgenticFactory` binds the project and definitions. It seeds root initial context
from `project.memory` by default; `seed_initial_messages_from_memory=False`
disables this. It constructs fresh log sinks at `project.logs / agent.name`.
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

## Workspace and capability inputs

Workspace areas describe roles, not permissions. Projects expose `logs`,
`snapshots`, and `memory`. Persistence paths may be project-relative or absolute
and must not overlap. `project.artifact_dir(name)` resolves an additional folder
inside the project. No dynamic configuration keys become Python attributes.

Applications select and configure `roboshed.capabilities`. RoboSprawl assembles
its capability tuple; any future user-facing selection belongs in Sprawl.
Capabilities do not depend on a named deployment profile.

File capabilities take concrete `WorkspacePermissions`; configure them from the
project before creating the definition. `Compactification` uses its owning
endpoint unless given another, and shares its pipe. Its default threshold is
80%; Sprawl explicitly chooses 60%.

## Migration

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
- Pass the project into `AgenticFactory(project=..., ...)`, then unpack
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
