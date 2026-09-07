# Agent definitions, factories, and ownership

Core describes and constructs agents. Shed supplies concrete agents and
capabilities; applications configure and host them.

| Module | Owns |
| --- | --- |
| `roboz.deployment` | `AgentDefinition`, `AgentCapability`, `Capability`, `SubAgentSpec` |
| `roboshed.agents` | `orchestrator()` and `librarian()`, both returning `AgentDefinition` |
| `roboshed.capabilities` | Reusable file and compaction capabilities, alongside `tools` and `skills` |
| Other Shed modules | Workspace/project structure, permissions, memory and file tools |
| Application | Configuration, model selection, permission policy, UI conventions, startup and shutdown |

Core imports none of these application modules.

## Capabilities and skills

Both `AgentDefinition` and preset callers configure `capabilities` only. A capability is a configured feature
such as file editing or compaction. It assembles the runtime tools and instructions
needed to provide that feature. Applications choose capabilities; they do not pass
parallel tool and skill lists into `orchestrator()` or `build_assistant()`.

A `Skill` is a lower-level package of instructions and optional tools. Capabilities
can supply skills for on-demand loading or session-start loading, expose tools
directly, or install automatic tools. These are implementation choices inside a
capability, represented by `Capability`; they are not competing definition or
preset inputs.
For example, `FileEditing` bundles a guarded patch tool with its editing instructions.
`Compactification` supplies an automatic tool without a skill.

The orchestrator and assistant always supply `stop`. `Agent` supplies user
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

A capability implements `build(pipe, agent_endpoint) -> Capability`. Its
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

## Agent presets

`roboshed.agents.orchestrator()` and `librarian()` both return `AgentDefinition`.
The orchestrator supplies a persistent collaboration prompt and a stop capability;
completing one task does not end the session. The librarian supplies its fixed
snapshot, consolidation and retention pipeline as an automatic capability.

Configure `orchestrator(agent_endpoint=endpoint, capabilities=(...))`. Configure
`librarian(project=project, agent_names=root.agent_names(), snapshot_endpoint=endpoint)`.
Call `.build()` on either definition and invoke the resulting agent directly.
Preset definitions select no event sinks; callers supply persistence explicitly
through `event_sink_factory`. The librarian can run in the background through
`run_background_agent`; hosts own cancellation and shutdown.

`roboshed.assistant.build_assistant` is a task-oriented file preset. It supplies
file capabilities and stop, with optional additional capabilities. Its demo builds
email tools and their orientation skill together inside one capability.

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

- Memory tools and the Librarian move from core to `roboshed.tools` and
  `roboshed.agents`. Replace `LibrarianConstructor` and its path record with
  `librarian(project=..., agent_names=..., snapshot_endpoint=...)` and `.build()`.
- Import `WorkspacePermissions`, `Workspace`, and `Project` from `roboshed.workspace`.
  Project identity and persistence paths are explicit; arbitrary configuration
  keys do not become Python attributes.
- `build_assistant` takes `project`, optional `permissions`, and `capabilities`.
  Move former `tool_builders`, tools and skill extensions into capability builds.
  The demo uses `WORKSPACE/projects/assistant`; `--data-path` selects log storage.
- Core keeps construction, control and interaction primitives. All tool/skill
  definition extensions use capabilities. There are no compatibility imports.
