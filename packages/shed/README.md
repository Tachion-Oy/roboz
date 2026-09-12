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
    agent_endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "done", "value": "Ready."}
    ]),
    capabilities=(
        Capability(tools=(stop,)),
        FileCommands(permissions),
        FileEditing(permissions),
    ),
).build()
result, messages = agent.invoke()
```

`roboshed.capabilities` provides `FileCommands`, `FileEditing`, `Compactification`,
`ConversationSnapshots`, `MemoryConsolidation`, `ArtifactRetention`, and
`MaintenanceCadence`, alongside the `tools` and `skills` modules. Applications
choose capabilities through each agent definition's `capabilities` tuple.
A capability owns its tools and skills. Generic `DeployableAgent` definitions
live in `roboz.deployment`.
Reusable `orchestrator` and `librarian` constructors live in `roboshed.agents`.
The orchestrator owns stop and guarded file work, and the Librarian owns
snapshots, consolidation, retention, and cadence. Root-only application
capabilities go in `Deployment.additional_capabilities`, after the agent's
built-in capability loop, without unpacking the role defaults.

Each capability uses an explicit endpoint override or its agent's default.
Runtime controls remain separate. Direct standalone builds accept explicit
`PermissionPolicy` and sandbox inputs.

See the [factory and migration guide](../../docs/agent-factories.md).

## RoboSprawl deployment recipe

`roboshed.deployments.robosprawl.RoboSprawl` configures the concrete persistent
orchestrator and Librarian recipe. Construct it with `memory_endpoint`,
`additional_capabilities`, `subagents`, and `interaction_mode`. Call the object
with an already-scoped sandbox, matching project slug, `endpoint_getter`, and
optional `event_sinks` to receive a fresh unbuilt `Deployment`.

The recipe loads project memory and supplies project locations through initial
messages. Its root follows the selected model getter; the Librarian uses its
separate memory endpoint. Construction starts no agents and creates no
directories. The application owns scope selection and runtime lifecycle.

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
status also carries the summary for event persistence. Each constructed tool
owns its compaction count; constructing one per agent keeps counters independent.

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

Low-level tool factories now use `roboz.Ctx(**values)` directly, with service,
endpoint, and executable objects supplied without wrappers. The specialized
context classes have been removed. Existing tool builders retain their keyword
arguments, defaults, permission checks, cancellation, and timeout behavior.
See the [migration guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/context-migration.md)
for low-level context fields and state ownership.


## Deployment instances

Declare a `Deployment(agent=..., sandbox=...)` from `roboshed.deployments`.
The root definition owns all recursive `subagents` and `background_agents`;
both slots contain the same definition type. Configure the attached objects in
place before building:

```python
deployment.sandbox.configure_scope(folder)
deployment.event_sinks.append(dispatch)
agent, background_agents = deployment.build()
```

Construct each application sandbox directly:

```python
sandbox = Sandbox(root=application_root, shared="workspace")
sandbox.configure_scope(folder)
```

The host supplies `folder` at runtime. For now it is a direct child of the
sandbox's existing `projects_dir`, with unchanged tiered permission behavior.
Default persistence paths follow that scope, and CLI sinks are disabled unless
requested. Startup memory is agent configuration, not deployment configuration.
Each build captures its scope and sink registrations, so later reconfiguration
cannot redirect an existing agent.

The Librarian constructor declares its standard maintenance sequence:
snapshots, consolidation, retention, then cadence. Pass its sandbox and the
recursive foreground names directly to the constructor before attaching it as
a background agent. The orchestrator also takes the configured sandbox and
captures its permission policy when constructed.

Invoke the returned agent directly and retain the background agents for control.
Do not reconfigure the same deployment concurrently. Repeated builds create fresh
runtimes and built-in persistence sinks, but supplied endpoint/sink objects remain
caller-owned. The old factories, host protocols, and result bundles are removed.
Use core's `roboz.llm.ModelSelector` for lazy model selection.
`roboz.dependencies` supplies exact dependency registration and binding;
`roboshed.dependency_health` supplies isolated `inspect_dependencies`, probes,
and monitoring without a web framework or provider SDK.
Permission policies treat configured folder names literally. Health scheduling
retries observation failures; timed-out workers retain their concurrency slots
until completion.
See [agent factories](../../docs/agent-factories.md) for the contracts and examples.

The `robosprawl` skill from `roboshed.skills` covers sandbox orientation and the
HUD file-link/markdown contract. The external RoboSprawl application may select
it through `Capability(auto_loaded_skills=(robosprawl,))`. Concrete paths,
endpoints, extra capabilities, and specialist definitions remain application
choices. The `RoboSprawl` recipe assembles them into a fresh `Deployment`.
