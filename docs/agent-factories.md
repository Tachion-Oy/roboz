# Deployable agents and application assembly

Roboz owns configurable recursive agent definitions. Shed supplies reusable
roles, sandbox-aware capabilities, and the fixed RoboSprawl recipe. Applications
select paths, endpoints, additional capabilities, specialists, and event sinks,
then retain the returned root and background agents for lifecycle control.

| Module | Responsibility |
| --- | --- |
| `roboz.deployment` | `DeployableAgent`, `AgentCapability`, and `Capability` |
| `roboshed.agents` | Reusable orchestrator and Librarian definitions |
| `roboshed.capabilities` | File, compaction, snapshot, memory, and retention features |
| `roboshed.sandbox` | Sandbox layouts and permission policies |
| `roboshed.deployments.robosprawl` | Fixed persistent orchestrator and Librarian recipe |
| Application | Concrete configuration and runtime lifecycle |

## Agent configuration

`DeployableAgent` is an ordinary configuration class. Its constructor sets the
agent's identity and behavior, fixed default capabilities, and initial child
definitions. Endpoints and other runtime values may be supplied later.

```python
from roboz import stop
from roboz.deployment import Capability, DeployableAgent

definition = DeployableAgent(
    name="worker",
    description="Completes a bounded delegated task.",
    system_prompt="Complete the task, report the result, then stop.",
    default_capabilities=(Capability(tools=(stop,)),),
)
definition.set_agent_endpoint(worker_endpoint)
definition.set_interaction_mode(interaction_mode)
definition.set_initial_messages((project_context,))

agent, background_agents = definition.build()
```

Constructor capabilities cannot be removed or replaced. Extend a definition
with `add_capabilities()`, `add_subagents()`, and `add_background_agents()`.
Their public views are read-only tuples, and additions preserve declaration
order. A subagent becomes a named delegation tool; a background agent becomes
an automatic start tool.

Every `build()` validates the complete graph, creates fresh runtime agents,
pipes, and capability bindings, and returns `(root, background_agents)` without
starting work. Caller event sinks follow foreground branches. An
`event_sink_factory` can supply fresh sinks for each agent name, including
background agents. Runtime state is never stored on `DeployableAgent`.

## Capability requirements

Capabilities declare configuration expected from their owning definition and
receive that definition when binding:

```python
from roboshed.sandbox import PermissionPolicy
from roboz.deployment import Capability, DeployableAgent, RequiredAttributes
from roboz.runtime import EventPipe

class ProjectFiles:
    @property
    def required_attributes(self) -> RequiredAttributes:
        return {"permissions": PermissionPolicy}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        permissions = agent.permissions
        return Capability(tools=(make_file_tool(permissions, pipe),))
```

Use `set_attributes()` for these capability-specific values:

```python
definition.set_attributes(permissions=project_permissions)
```

Configuration may remain incomplete while definitions are declared. `validate()`
and `build()` report every missing, `None`, or incorrectly typed requirement,
including the owning agent and capability. Empty collections, `False`, zero,
and other correctly typed falsey values are valid. Structural names, methods,
and private attributes cannot be overwritten through `set_attributes()`.

Attributes are local to one definition. A parent's permissions, sandbox, or
other values are never inherited by children; configure each owning node
explicitly. Already-bound `Capability` objects require no owner attributes and
retain caller ownership of their supplied tools and skills.

Shed capabilities use these owner values:

| Capability | Owner configuration |
| --- | --- |
| `FileCommands`, `FileEditing` | `permissions` |
| `ConversationSnapshots`, `MemoryConsolidation`, `MaintenanceCadence` | `sandbox`, `watched_agent_names` |
| `ArtifactRetention` | `sandbox` |
| Model-backed capabilities without an endpoint override | `agent_endpoint` |

Thresholds, limits, timeouts, cadence, skill-loading choices, and explicit
endpoint overrides remain on the capability object.

## Shed roles

The orchestrator owns its identity, persistent-collaborator prompt, `stop`, and
guarded file capabilities. The Librarian owns its deterministic settings and
ordered maintenance pipeline: snapshot conversations, consolidate memory,
apply artifact retention, then wait or stop when the project is idle.

```python
from roboshed.agents import librarian, orchestrator

root = orchestrator(sandbox, agent_endpoint=main_endpoint, subagents=(researcher,))
watched_names = root.agent_names(include_background=False)
root.add_background_agents(
    librarian(sandbox, watched_names, agent_endpoint=memory_endpoint)
)
root.add_capabilities(application_capability)

agent, background_agents = root.build(event_sink_factory=sinks_for_agent)
```

Both roles require an already-scoped sandbox. The orchestrator derives its own
permission policy. The Librarian stores its sandbox and complete recursive
foreground names on its definition. Snapshot and consolidation capabilities
default to the Librarian's endpoint.

## Sandbox scope and persistence

Create a separate scoped sandbox for each runtime graph:

```python
from pathlib import Path
from roboshed.sandbox import Sandbox

configured_sandbox = Sandbox(
    root=application_root,
    shared="workspace",
    logs=Path("conversation_logs"),
    snapshots=Path("conversation_snapshots"),
    memory=Path("persistent_memory"),
)
sandbox = configured_sandbox.for_project(folder)
```

Scope selection validates paths without creating directories. Keep that scoped
instance unchanged for the graph's lifetime. Project folders cannot be symbolic
links. The derived permission policy allows reads throughout the sandbox, writes
inside the selected project, asks before shared-area writes, and denies other
writes.

## Fixed RoboSprawl recipe

`roboshed.deployments.robosprawl.RoboSprawl` is a standalone configuration class,
not a `DeployableAgent` subclass. It can be created before runtime inputs are
known. Supply them through setters before calling argument-free `build()`:

```python
from roboshed.capabilities import Compactification
from roboshed.deployments.robosprawl import RoboSprawl
from roboshed.skills import robosprawl as orientation
from roboz.deployment import Capability
from roboz.runtime import Output

recipe = RoboSprawl()
recipe.set_sandbox(sandbox)  # Already scoped with sandbox.for_project(name).
recipe.set_endpoint_getter(selected_endpoint_getter)
recipe.set_memory_endpoint(memory_endpoint)
recipe.set_additional_capabilities(
    (
        Capability(auto_loaded_skills=(orientation,)),
        Compactification(threshold_percent=60.0),
    ),
)
recipe.set_specialists(application_specialists)
recipe.set_interaction_mode(Output.API)
recipe.set_event_sinks((ui_event_sink,))
agent, background_agents = recipe.build()
```

The sandbox, endpoint getter, and memory endpoint are required at build time;
the other inputs default to empty sequences and `interaction_mode=None`. A `None`
mode inherits core's current output setting, falling back to CLI when none is
bound. Missing inputs raise `ValueError` before composition. Configuration and
building neither invoke
agents nor materialize provider clients nor create project directories.

Each build creates a fresh configuration graph and then fresh runtimes.
The recipe owns the orchestrator defaults and the Librarian; callers cannot
replace either or alter the Librarian pipeline. Additional capabilities append
after the orchestrator defaults. Watched names are calculated only after all
specialists are attached. The root follows model switching through the endpoint
getter, while the Librarian retains its separate memory endpoint.

Create a new recipe for each new run, not for replies or stream reconnects.
Setters snapshot the sandbox and sequence containers; endpoints and supplied
capability/child objects remain caller-owned. Do not concurrently reconfigure
one shared recipe. Reconfiguring a recipe does not redirect an already-built
runtime's sandbox or sinks.

Migration: replace the old callable dataclass or function call with the setters
above. `robosprawl` remains an importable alias for `RoboSprawl`, but does not
preserve the old argument-taking signature. No `Deployment` wrapper is needed.

Project memory and locations remain initial messages. Each agent receives its
own persistence sink below the scoped conversation-log directory, while caller
sinks reach foreground branches only. The application owns invocation,
interruption, cancellation, background shutdown, and dependency health.

`roboshed.dependency_health.inspect_dependencies()` accepts a callback that
builds and returns the same `(root, background_agents)` tuple against its
temporary sandbox. It does not require a deployment wrapper.
