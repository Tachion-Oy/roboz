# Agent definitions and capabilities

`roboz.deployment` provides `AgentDefinition`, `AgentCapability`, `Capability`,
and `SubAgentSpec`. Definitions accept capabilities as their only tool/skill
extension input; the runtime supplies user interaction. Callers supply stopping
through a capability when constructing a task-oriented agent.

A capability groups the tools and instructions that provide a feature. Already-bound
`Capability` objects and runtime-bound implementations share the same build contract.
A `Skill` is a lower-level package of instructions and optional tools supplied
inside a capability, with on-demand or session-start loading.

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
to the root definition's capability tuple. No separate tool-injection build argument exists.
