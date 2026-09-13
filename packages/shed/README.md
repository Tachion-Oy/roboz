# roboshed

Reusable agent factories, capabilities, sandbox policies, tools, and skills built on Roboz. Version `0.1.1.dev1`
is a development snapshot; APIs are unstable. Dependencies are Roboz and Pydantic only.

Includes guarded Unix file commands, Python patch editing, CLI/file/email
instructions, and provider-neutral email contracts and tools. It does not
install any model SDK, Proton, document SDK, web service, or backend framework.

```python
from pathlib import Path
from roboz import stop
from roboz.deployment import DeployableAgent, Capability
from roboz.llm import MockLLMEndpoint
from roboshed.capabilities import FileCommands, FileEditing
from roboshed.sandbox import PermissionPolicy, Sandbox

permissions = PermissionPolicy.local(Path("./sandbox"))
agent = DeployableAgent(
    name="file_worker",
    system_prompt="Complete the user's task, then call stop.",
    default_capabilities=(
        Capability(tools=(stop,)),
        FileCommands(),
        FileEditing(),
    ),
)
agent.set_agent_endpoint(MockLLMEndpoint([
    {"action": "stop", "rationale": "done", "value": "Ready."}
]))
agent.set_attributes(permissions=permissions)
agent, background_agents = agent.build()
result, messages = agent.invoke()
```

`roboshed.capabilities` provides `FileCommands`, `FileEditing`, `Compactification`,
`ConversationSnapshots`, `MemoryConsolidation`, `ArtifactRetention`, and
`MaintenanceCadence`, alongside the `tools` and `skills` modules. Applications
choose fixed capabilities through `default_capabilities` and append application
extensions with `add_capabilities()`.
A capability owns its tools and skills. Generic `DeployableAgent` definitions
live in `roboz.deployment`.
Reusable `orchestrator` and `librarian` constructors live in `roboshed.agents`.
The orchestrator owns stop and guarded file work, and the Librarian owns
snapshots, consolidation, retention, and cadence. Root-only application
capabilities append after the agent's protected defaults, without unpacking the
role definitions.

Each capability declares the typed attributes it reads from its owning
`DeployableAgent`. Runtime controls remain separate. Use `set_attributes()` to
supply standalone permission policies and sandbox inputs before building.
Capability builders construct the central typed tool contexts; each build gets
fresh runtime state while retaining the selected endpoint objects. Build-based
resource inspection uses those tools without initializing model clients.

See the [factory and migration guide](../../docs/agent-factories.md).

## RoboSprawl deployment recipe

The recipe still requires migration to the concrete endpoint contract in this
branch; importing it currently fails on the removed lazy-reference API. The
standalone `orchestrator` and `librarian` constructors and their capabilities are
migrated. The description below records the recipe behavior to preserve.

`roboshed.deployments.robosprawl.robosprawl` is the concrete lazy persistent
orchestrator and Librarian recipe. Call it with an already-scoped sandbox,
`endpoint_getter`, `memory_endpoint`, `additional_capabilities`, `specialists`,
`interaction_mode`, and optional `event_sinks`. It returns a fresh root and
background-agent tuple.

The recipe loads project memory and supplies project locations through initial
messages. Its root follows the selected model getter; the Librarian uses its
separate memory endpoint. Construction starts no agents and creates no
directories before the build requires its persistence sinks. The application
owns scope selection and runtime lifecycle.

## Conversation compaction

The following fragment belongs inside an agent or tool builder. `endpoint` is
the selected compaction model, and `agent_pipe` is the owning agent's event
pipe. These are independent inputs to the tool.

```python
from roboshed.tools import get_compactify_messages_when_needed_tool

compact = get_compactify_messages_when_needed_tool(
    endpoint=endpoint, threshold_percent=60, pipe=agent_pipe, timeout_s=60,
)
```

Include this tool in an agent's `default_tools` and pass that agent's owning
`EventPipe`. The standalone factory defaults to an 80% threshold and no timeout;
`system_prompt` and `skill_message` override the full continuation instructions.
The tool preserves the contiguous bootstrap prefix and folds the remaining
history, including previous summaries, into a new continuation message. Its
status also carries the summary for event persistence. Each capability build
creates a fresh compaction context; tools copied or rebound to that context share
its count, while separate builds keep counters independent.

Successful status reports describe the compacted history's current usage and
headroom. Summaries are budgeted below the configured threshold and endpoint
capacity, including the preserved prefix and continuation payload. If there is
no room for a summary, or the returned replacement still exceeds the budget
after summarization retries, the tool returns `blocked` without changing history
or the counter. The continuation payload retains `percent_used_before`.

Cancellation and interruption propagate through the existing Roboz summarizer.
An optional positive, finite `timeout_s` bounds each provider attempt, not the
whole compaction. Failed attempts leave history and the counter unchanged;
late provider results are ignored without forcibly killing worker threads.
Summarization messages and model-call events use the supplied pipe.

The public tool name and persisted caller are `compactify_messages_when_needed`.
This tool owns the shared continuation prompts and retains RoboSprawl's caller
name. The old `robosprawl.compaction` import is
replaced by `roboshed.tools`. Shed also owns the shared summarizer and Librarian
memory pipeline; core provides the mechanisms they use.

## Context API migration

Low-level tool factories use concrete context classes from `roboshed.tools`.
Their typed constructors own required fields, defaults, validation, and fresh
state. Direct resources such as endpoints may also be factory contexts. Existing
tool builders retain their keyword arguments, permission checks, cancellation,
and timeout behavior. See the
[context guide](../../docs/shed-tool-contexts.md) for the complete mapping.


## Deployable agent graphs

The root `DeployableAgent` owns recursive `subagents` and `background_agents`;
both slots contain the same definition type. Configure each object explicitly
before building:

```python
sandbox.configure_scope(folder)
definition.set_attributes(permissions=sandbox.permissions())
agent, background_agents = definition.build(event_sinks=(dispatch,))
```

Construct each application sandbox directly:

```python
sandbox = Sandbox(root=application_root, shared="workspace")
sandbox.configure_scope(folder)
```

The host supplies `folder` at runtime. For now it is a direct child of the
sandbox's existing `projects_dir`, with unchanged tiered permission behavior.
Default persistence paths follow that scope. Startup memory and endpoints are
agent configuration. Each build creates fresh runtime state; applications can
supply an agent-specific sink factory for persistence.

The Librarian constructor declares its standard maintenance sequence:
snapshots, consolidation, retention, then cadence. Pass its sandbox and the
recursive foreground names directly to the constructor before attaching it as
a background agent. The orchestrator also takes the configured sandbox and
captures its permission policy when constructed.

Invoke the returned agent directly and retain the background agents for control.
Repeated builds create fresh runtimes and bindings, but supplied endpoints,
capabilities, and sinks remain caller-owned. No execution state is retained on
the definition. The old deployment wrapper, factories, host protocols, and
result bundles are removed.
Use core's `roboz.llm.ModelSelector` for lazy model selection.
`roboz.dependencies` supplies the resource contract and ordered deduplication.
`DeployableAgent.external_dependencies()` builds an unstarted graph and inspects
its resources. `roboshed.dependency_health` monitors resource-owned checks without a
web framework or provider SDK.
Permission policies treat configured folder names literally. Health scheduling
retries observation failures; timed-out workers retain their concurrency slots
until completion.
See [agent factories](../../docs/agent-factories.md) for the contracts and examples.

The `robosprawl` skill from `roboshed.skills` covers sandbox orientation and the
HUD file-link/markdown contract. The external RoboSprawl application may select
it through `Capability(auto_loaded_skills=(robosprawl,))`. Concrete paths,
endpoints, extra capabilities, and specialist definitions remain application
choices. The `robosprawl` recipe assembles and builds a fresh agent graph.


## Dependency health

Call `definition.external_dependencies()` on the configured `DeployableAgent`.
It constructs fresh agents without event sinks and delegates to their existing
tool dependency inspection, including foreground and background descendants and
unloaded skills. It returns a deduplicated `tuple[ExternalDependency, ...]` and
does not invoke agents or request endpoint initialization or availability checks.
Capability builders run normally, including any construction effects they own.

The former `inspect_dependencies` callback helper and its temporary sandbox are
removed. The optional definition method requires configuration sufficient for
normal construction; the monitor never calls it automatically. Agents do not need
to be running. Existing runtime agents can still be inspected directly. If inputs
are substituted for discovery, they must produce the resource declarations used
by the actual deployment.

The monitor accepts resources independently of agents. Combine agent resources
with other resources explicitly, for example:

```python
monitor = DependencyHealthMonitor((
    *definition.external_dependencies(),
    *selectable_models,
    transcription_endpoint,
))
```

Here `selectable_models` is a sequence of concrete `LLMEndpoint` objects; they need
not be attached to an agent or selected yet. The monitor keeps the first resource
for each dependency ID across the combined sequence. Resources are captured when
the monitor is constructed; create a new monitor if the resource set changes.

Pass those resources directly to `DependencyHealthMonitor(resources)`. Its
constructor creates pending records without performing checks. Explicit
`run_once()` or scheduled observation calls each resource's synchronous
`check() -> bool` in a worker thread. Checks own their service-specific behavior
and any required client initialization; the monitor supplies bounded concurrency,
timeouts, scheduling, and cached observations.

```python
import asyncio
import sys

from roboz.dependencies import ExecutableDependency
from roboshed.dependency_health import DependencyHealthMonitor, DependencyStatus

program = ExecutableDependency(sys.executable)
monitor = DependencyHealthMonitor((program,))
assert monitor.records()[0].status is DependencyStatus.PENDING
asyncio.run(monitor.run_once())
assert monitor.records()[0].status is DependencyStatus.AVAILABLE
```

`check_dependency(resource)` performs one synchronous check and returns a
`DependencyCheckResult`: `True` means available, `False` becomes `model_unavailable`
for models or `not_found` for other resources, and exceptions become sanitized
reason codes. Other return values produce `protocol_error`. Provider exception
payloads are not exposed through records. Record schemas and metadata filtering
are unchanged. Async checker callbacks and registration records are removed;
implement the synchronous method on the resource instead.

Replace `check_executable`, `check_openai_compatible_endpoint`, and
`check_network_service` with `check_dependency` when a sanitized health result
is needed, or use `resource.check()` for the primitive boolean/exception contract.
The old helper names have no compatibility aliases. Capability bindings and the
RoboSprawl deployment recipe now use the concrete context and endpoint contracts.

## Concrete tool contexts

File-command, guard, editing, maintenance, and email factories now use concrete context
classes exported from `roboshed.tools`. Existing `get_run_file_command`,
`get_apply_patch`, and `get_compactify_messages_when_needed_tool` keyword arguments
are retained. Direct factory users should follow the
[context migration guide](../../docs/shed-tool-contexts.md), including the context
ownership rules for compaction counters. Command and summary resources are
reported through `tool.external_dependencies()` without running external work.


Email contexts live in the same module. `get_work_with_email` keeps its existing
arguments; direct email execution uses `EmailContext`, and attachment resolvers
accept `Path`. `EmailService` defines every provider operation and the resource
identity/metadata contract. Its availability check calls the existing read-only
probe. See the [email context contract](../../docs/shed-tool-contexts.md#email-services-and-contexts).
