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

`roboshed.deployments.robosprawl.robosprawl()` is the lazy fixed recipe. It
accepts a scoped sandbox, selected endpoint getter, separate memory endpoint,
permitted root additions, specialists, interaction mode, and caller sinks:

```python
from roboshed.capabilities import Compactification
from roboshed.deployments.robosprawl import robosprawl
from roboshed.skills import robosprawl as orientation
from roboz.deployment import Capability
from roboz.runtime import Output

agent, background_agents = robosprawl(
    sandbox,
    endpoint_getter=selected_endpoint_getter,
    memory_endpoint=memory_endpoint,
    additional_capabilities=(
        Capability(auto_loaded_skills=(orientation,)),
        Compactification(threshold_percent=60.0),
    ),
    specialists=application_specialists,
    interaction_mode=Output.API,
    event_sinks=(ui_event_sink,),
)
```

Each call creates a fresh configuration graph and then builds fresh runtimes.
The recipe owns the orchestrator defaults and the Librarian; callers cannot
replace either or alter the Librarian pipeline. Additional capabilities append
after the orchestrator defaults. Watched names are calculated only after all
specialists are attached. The root follows model switching through the endpoint
getter, while the Librarian retains its separate memory endpoint.

Project memory and locations remain initial messages. Each agent receives its
own persistence sink below the scoped conversation-log directory, while caller
sinks reach foreground branches only. The application owns invocation,
interruption, cancellation, background shutdown, and dependency health.

## Inspect before invocation

Use the configured `DeployableAgent` as the inspection surface:

```python
from roboz import Str, stop
from roboz.deployment import Capability, DeployableAgent
from roboshed.dependency_health import DependencyHealthMonitor


definition = DeployableAgent(
    name="worker", is_agentic=False,
    default_capabilities=(Capability(default_tools=(stop,)),),
)
resources = definition.external_dependencies()
monitor = DependencyHealthMonitor(resources)
agent, background_agents = definition.build()
result, messages = agent.invoke(input=Str(value="done"))
assert resources == ()
assert result.value == "done"
```

`external_dependencies()` calls `build()` without event sinks, then delegates to
`Agent.external_dependencies()`. The root agent already includes its foreground
and background children through their bound tool contexts, as well as default,
active, passive, and unloaded skill tools. Equal dependency IDs retain the first
resource in the agent's inspection order. Capabilities continue to implement
`required_attributes` and `build`; their tools supply the dependency information.

Each inspection validates the current configuration and constructs fresh runtime
agents, pipes, and capability bindings. It does not invoke agents, read their
initial messages, or request endpoint initialization or availability checks.
Custom capability builders execute normally: construction effects and errors
remain possible. This method does not isolate filesystem access. Keep external
operations in tool invocation or explicit resource checks when authoring builders.

The former `roboshed.dependency_health.inspect_dependencies()` callback helper,
including its temporary sandbox, is removed. The definition inspection method
is optional and requires complete build configuration. It is not called by the
monitor or by the existing deployment lifecycle. Discovery uses normal construction
to determine the actual tool graph. Agents need not be running. Any substituted
configuration used for discovery must produce the same resource declarations as
the actual deployment. If runtime agents already exist, their inspection methods
remain available.

The health monitor also accepts dependencies unrelated to an agent. For example,
combine `(*definition.external_dependencies(), *selectable_models, transcription)`
when constructing `DependencyHealthMonitor`. This can check every selectable
model before an invocation chooses one. The monitor deduplicates the combined
resources and checks them only when observation runs; see the
[Shed health guide](../packages/shed/README.md#dependency-health).

Shed's file, maintenance, and email tool contexts and capability bindings are
migrated. Capabilities construct central typed contexts while preserving their
arguments, owner configuration, endpoint overrides, defaults, and tool order.
The RoboSprawl recipe and live model selection migration remain in progress.
