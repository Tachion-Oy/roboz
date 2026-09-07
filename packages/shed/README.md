# roboshed

Reusable tools, skills, and a small assistant built on Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Dependencies are Roboz and Pydantic only.

Includes guarded Unix file commands, Python patch editing, CLI/file/email
instructions, and provider-neutral email contracts and tools. It does not
install any model SDK, Proton, document SDK, web service, or backend framework.

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboshed.assistant import WorkspacePermissions, build_assistant

assistant = build_assistant(
    endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "Complete", "value": "Hello"}
    ]),
    workspace=WorkspacePermissions.local(Path("./workspace")),
)
result, messages = assistant.invoke()
```

`build_assistant` accepts additional tools, skills, event sinks, initial messages,
and a system prompt. `tool_builders` receive the owning event pipe to bind
cancellation and events. Low-level `get_run_file_command`, `get_apply_patch`,
and `get_work_with_email` factories also work without this assistant.

The assistant supplies read commands and patch editing. The file factory can
also explicitly enable its write/delete command specifications. Permission
rules cover allow/deny/ask, configured precedence, overwrite checks, and resolved
paths; they are not an OS sandbox. Commands require their named Unix executables.

Generic guard models preserve input/payload types without integration-specific
unions. Use concrete generic parameters when decoding serialized guard results.

The installed demo requires only `cat` in mock mode:

```bash
python -m roboshed.demo --mock --workspace /tmp/roboz-demo-workspace --data-path /tmp/roboz-demo-data
```

It creates a uniquely named file and a saved conversation. Its real-provider
and email flags require the separately installed adapters. See the repository's
[installation guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/addons.md)
for local wheel installation before these packages are published.

## Conversation compaction

The following fragment belongs inside an agent or tool builder. `endpoint` is
the agent's configured endpoint, and `agent_pipe` is its owning event pipe
(supplied to `tool_builders` by `build_assistant`).

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
replaced by `roboshed.tools`; Roboz core continues to own the shared summarizer.

## Context API migration

Low-level tool factories now use `roboz.Ctx(**values)` directly, with service,
endpoint, and executable objects supplied without wrappers. The specialized
context classes have been removed. Existing tool builders retain their keyword
arguments, defaults, permission checks, cancellation, and timeout behavior.
See the [migration guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/context-migration.md)
for low-level context fields and state ownership.
