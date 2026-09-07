# roboshed

Reusable agent factories, capabilities, workspaces, tools, and skills built on Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Dependencies are Roboz and Pydantic only.

Includes guarded Unix file commands, Python patch editing, CLI/file/email
instructions, and provider-neutral email contracts and tools. It does not
install any model SDK, Proton, document SDK, web service, or backend framework.

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboshed.workspace import Project, Workspace
from roboshed.assistant import build_assistant

agent = build_assistant(
    endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "done", "value": "Ready."}
    ]),
    project=Project(Workspace(Path("./workspace")), "example"),
)
agent.invoke()
```

`roboshed.capabilities` provides `FileCommands`, `FileEditing`, and
`Compactification`, alongside the `tools` and `skills` modules. Applications
choose and configure these capabilities through the presets’ single `capabilities`
extension argument. A capability owns its tools and any skills used for instructions. `roboshed.deployments.robosprawl`
provides project composition. `roboshed.agents` supplies the reusable
orchestrator/Librarian presets. Generic definitions and
capability contracts live in `roboz.deployment`. Each capability
builds against the owning agent's pipe. Select permission policies through
`roboshed.workspace.WorkspacePermissions`; workspace structure does not grant
access. `build_assistant` is a task-oriented preset using the same construction.

See the [factory and migration guide](../../docs/agent-factories.md).

## Conversation compaction

The following fragment belongs inside an agent or tool builder. `endpoint` is
the agent's configured endpoint, and `agent_pipe` is its owning event pipe
(supplied to each capability by the agent definition).

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
This tool incorporates the continuation prompts used by PeffaHub/PeffaShed while
retaining RoboSprawl's caller name. The old `robosprawl.compaction` import is
replaced by `roboshed.tools`. Shed also owns the shared summarizer and Librarian
memory pipeline; core provides the mechanisms they use.

## Context API migration

Low-level tool factories now use `roboz.Ctx(**values)` directly, with service,
endpoint, and executable objects supplied without wrappers. The specialized
context classes have been removed. Existing tool builders retain their keyword
arguments, defaults, permission checks, cancellation, and timeout behavior.
See the [migration guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/context-migration.md)
for low-level context fields and state ownership.

Capability builders receive `build(pipe, *, default_endpoint)`. Configure
individual tool models on their capabilities; the agent endpoint supplies a
default, independently of the runtime pipe.
