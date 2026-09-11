# Deployable agents and application deployments

Roboz owns generic recursive agent definitions. Shed owns reusable agent roles,
sandbox-aware capabilities, and deployment assembly. Applications own concrete
paths, endpoints, agent graphs, extra capabilities, context, sinks, and the final
deployment instance.

| Module | Responsibility |
| --- | --- |
| `roboz.deployment` | `DeployableAgent` and capability contracts |
| `roboshed.agents` | Reusable `orchestrator` and `librarian` constructors |
| `roboshed.capabilities` | File, compaction, and maintenance capabilities |
| `roboshed.sandbox` | Reusable sandbox layouts, runtime scope, and permission policy |
| `roboshed.deployments` | `Deployment(agent, sandbox, event_sinks, include_cli_output)` |
| Application | Concrete configuration and runtime lifecycle |

There is no RoboSprawl deployment preset in Shed. Importing Shed selects no
application path, endpoint, agent graph, or deployment instance.

## Agent definitions

Construct generic definitions directly. Named roles use ordinary functions in
their owning distribution.

```python
from roboz import stop
from roboz.deployment import Capability, DeployableAgent

definition = DeployableAgent(
    name="worker",
    description="Completes a bounded delegated task.",
    system_prompt="Complete the task, report the result, then stop.",
    capabilities=(Capability(tools=(stop,)),),
    agent_endpoint=worker_endpoint,
)
agent = definition.build()
```

The generic dataclass constructor remains available for one-off definitions.
Both child slots contain `DeployableAgent` values: `subagents` become selectable
delegation tools and `background_agents` become automatic start tools. Builds
validate names recursively and return fresh runtime agents without invoking them.
Use `definition.build_graph()` to receive `(agent, background_agents)` when the
host needs background handles for lifecycle control; `build()` returns only the
root agent. Neither method selects sandbox paths or persistence sinks.

## Shed agent roles

Import the reusable constructors from `roboshed.agents`:

```python
from dataclasses import replace

from roboshed.agents import librarian, orchestrator

root = orchestrator(
    sandbox,
    agent_endpoint=main_endpoint,
    subagents=(researcher,),
)
agent_names = root.agent_names(include_background=False)
root = replace(
    root,
    background_agents=(
        librarian(sandbox, agent_names, agent_endpoint=memory_endpoint),
    ),
)
```

The orchestrator owns its name, description, persistent-collaborator prompt,
`stop`, guarded file commands, and file editing. The Librarian owns its name,
description, deterministic non-agentic settings, disabled automatic tool prompt,
and its standard ordered maintenance pipeline:

1. snapshot foreground conversations;
2. consolidate pending snapshots into memory;
3. apply log, snapshot, and memory retention;
4. wait between cycles or stop when the project is idle.

The application supplies root-only additions through
`Deployment.additional_capabilities`; they follow the orchestrator's defaults.
RoboSprawl therefore needs no `ORCHESTRATOR_CAPABILITIES` constant and does not
manually copy either role's built-in capabilities.

Both roles require an already-configured application sandbox. The Librarian also
requires the names of the configured foreground graph; compute them before
attaching it as a background agent. Its snapshot and consolidation models
default to its `agent_endpoint`.

## Sandbox definitions and runtime scope

Construct the sandbox directly and select its scope before constructing agents:

```python
from pathlib import Path
from roboshed.sandbox import Sandbox

sandbox = Sandbox(
    root=application_root,
    shared="workspace",
    logs=Path("conversation_logs"),
    snapshots=Path("conversation_snapshots"),
    memory=Path("persistent_memory"),
)
sandbox.configure_scope(folder)
```

The host supplies `root` from its real configuration and selects a direct child
of `projects_dir` with `configure_scope(folder)` before constructing agents.
Relative
persistence paths follow that scope. The derived policy allows reads throughout
the sandbox, writes in the selected project, asks before shared-area writes, and
denies other writes. Construction and scope selection create no directories.

Standalone file agents can pass `PermissionPolicy` directly to `FileCommands`
and `FileEditing`.

For independent runs, retain the configured layout and create a separate scoped
instance before constructing each graph:

```python
configured_sandbox = Sandbox(root=application_root, shared="workspace")
sandbox = configured_sandbox.for_project(folder)
```

`for_project()` validates the requested project and persistence layout without
changing the source instance or creating directories. Pass the returned instance
to the root, relevant specialists, Librarian, and `Deployment`; keep its scope
unchanged throughout that graph's lifetime. File capabilities receive policies
derived from this instance; maintenance capabilities use it for their paths.

## Application-owned RoboSprawl configuration

The external RoboSprawl application should keep the following composition in
its own configuration module. Names here illustrate the ownership boundary;
the application supplies its actual `application_root`, endpoints, agents,
instructions, and UI event sink.

```python
from dataclasses import replace
from pathlib import Path

from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import Compactification
from roboshed.deployments import Deployment
from roboshed.sandbox import Sandbox
from roboshed.skills import robosprawl as orientation
from roboz.deployment import Capability
from roboz.runtime import Output

sandbox = Sandbox(
    root=application_root,
    shared="workspace",
    logs=Path("conversation_logs"),
    snapshots=Path("conversation_snapshots"),
    memory=Path("persistent_memory"),
)
sandbox.configure_scope(folder)
foreground = orchestrator(
    sandbox,
    agent_endpoint=orchestrator_endpoint,
    interaction_mode=Output.API,
    subagents=application_subagents,
    initial_messages=(sandbox.project_memory_dir(), application_instructions),
)
agent_names = foreground.agent_names(include_background=False)
agent = replace(
    foreground,
    background_agents=(
        *application_background_agents,
        librarian(sandbox, agent_names, agent_endpoint=memory_endpoint),
    ),
)
deployment = Deployment(
    agent=agent,
    sandbox=sandbox,
    additional_capabilities=(
        Capability(auto_loaded_skills=(orientation,)),
        Compactification(endpoint=compaction_endpoint, threshold_percent=60.0),
    ),
    event_sinks=[ui_event_sink],
)

agent, background_agents = deployment.build()
```

Shed does not prescribe the application's project-context text or memory-loading
choice.

`Deployment.build()` is parameterless. It copies the configured sandbox,
captures its scope and sink registrations, appends
`additional_capabilities` to the root definition's capability sequence, and
returns `(agent, background_agents)`. Later deployment changes affect only later
builds.

Every runtime agent receives a fresh persistence sink rooted at its scoped log
directory. Caller event sinks follow foreground branches only. CLI output is
disabled by default and enabled with `include_cli_output=True`. Applications own
invocation, interruption, cancellation, thread shutdown, and dependency health.

## Migration summary

- Import role constructors from `roboshed.agents`, not a deployment-specific
  module.
- Replace `AgentDefinition` and `SubAgentSpec` with recursive
  `DeployableAgent` definitions.
- Replace RoboSprawl factories and recipes with an application-owned
  `Deployment(agent=..., sandbox=...)` instance.
- Replace shared or hardcoded sandbox instances with a directly constructed,
  application-owned `Sandbox` for each deployment.
- Pass the sandbox to both role constructors and pass recursive foreground names
  to `librarian`; compute those names before attaching it as a background agent.
- Call `sandbox.configure_scope(folder)` before constructing agents.
- Add root-only application capabilities through `Deployment`; do not unpack
  the orchestrator's defaults.
- Keep maintenance feature settings on their owning capability and endpoint
  overrides on model-backed capabilities.
- Import dependency inspection from `roboshed.dependency_health`; its callback
  receives a temporary sandbox and returns a configured `Deployment`.

This is an unreleased breaking API change. It does not add compatibility
wrappers for the removed RoboSprawl deployment presets or factory layers.
