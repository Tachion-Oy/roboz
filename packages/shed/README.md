# roboshed

Reusable agent factories, capabilities, workspaces, tools, and skills built on Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Dependencies are Roboz and Pydantic only.

Includes guarded Unix file commands, Python patch editing, CLI/file/email
instructions, and provider-neutral email contracts and tools. It does not
install any model SDK, Proton, document SDK, web service, or backend framework.

```python
from pathlib import Path
from roboz import stop
from roboz.deployment import AgentDefinition, Capability
from roboz.llm import MockLLMEndpoint
from roboshed.capabilities import FileCommands, FileEditing
from roboshed.workspace import WorkspacePermissions

permissions = WorkspacePermissions.local(Path("./workspace"))
agent = AgentDefinition(
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
choose and configure these capabilities through the presets’ single `capabilities`
extension argument. A capability owns its tools and any skills used for instructions. `roboshed.deployments.robosprawl`
provides project composition. `roboshed.agents` supplies the reusable
orchestrator/Librarian presets. Generic definitions and
capability contracts live in `roboz.deployment`. Each capability
builds its tools with explicit endpoint overrides or the agent's default.
Runtime controls remain separate from endpoints. The Librarian accepts an ordered
`capabilities` sequence. Snapshot and consolidation capabilities each expose an
`endpoint`; `agent_endpoint` supplies their shared default. Retention and cadence
need no model. Each capability owns its settings and project inputs. Select permission policies through
`roboshed.workspace.WorkspacePermissions`; workspace structure does not grant
access. Compose task-oriented agents directly with `AgentDefinition`.

See the [factory and migration guide](../../docs/agent-factories.md).

## File CLI argument parsing

Built-in file commands accept an explicit subset of their Unix argument grammar.
Options may follow file operands, and `--` ends option parsing. For example,
`cat notes.txt -n` checks `notes.txt`, and `cat -- -notes` checks the dash-named
file. `find` uses its own grammar: global options, search roots, then predicates;
write a dash-named root as `./-notes`.

Search patterns and option values are distinct from file operands. Use
`grep -e needle notes.txt`, `rg --max-count 20 needle src`, or
`rg -g '*.py' needle src`. `rg --files src` does not consume a pattern.
The stdin marker `-` remains stdin for read commands; for `tee` it names a file.
Copy/move destination parsing honors `--`, `-t DIR`, and `-T`.

Migration from the former permissive parser:

- Spell out long options; unknown or abbreviated options now return a parse
  error. Supply required values, and use `--` or `./` for dash-named files.
- Supply patterns directly using `-e`. Pattern-file options (`-f`/`--file`),
  explicit ignore/exclusion files, indirect file lists (`--files0-from`),
  reference-file options, and subprocess preprocessors are rejected.
- Supply copy/move target directories as separate literal tokens (`-t DIR` or
  `--target-directory DIR`). Globs that match no files return a parse error
  instead of disappearing from argv.
- Pass supported options in `argv`; execution ignores `POSIXLY_CORRECT` and
  `RIPGREP_CONFIG_PATH` so environment settings cannot change argument parsing.
- Recursive symlink-following options (`grep -R`/`--dereference-recursive` and
  `rg -L`/`--follow`) are rejected before execution because nested symlink
  targets are not individually authorized. Use `grep -r`/`--recursive` or plain
  `rg` for recursive search that skips nested symlinks, and pass any intended
  symlink targets as explicit file operands for permission checks.

Public input models and tool signatures are unchanged. Custom `CmdSpec`
extractors remain responsible for validating their executable's grammar.

These guards check explicit operands, or the working directory for implicit
inputs. They do not check each descendant visited by recursive commands or each
implicit configuration/ignore file. Fixing argument parsing does not make these
tools a process sandbox or resolve directory-traversal permission gaps.

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
