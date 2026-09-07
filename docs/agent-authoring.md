# Agent Authoring Guide

This guide is the opinionated path for creating robust agents with `roboz`.
It focuses on concrete runtime behavior and conventions already used in the repository.

Primary references:

- Core reference: [`reference.md`](reference.md)
- Tool and factory patterns: [`tool-authoring.md`](tool-authoring.md)

## Golden Path

Build agents in this order:

1. Define role and decision policy in `system_prompt`.
2. Define active tools only for choices that require model judgment.
3. Chain typed passive tools for follow-up work that code can decide.
4. Define startup/default tools.
5. Attach skills (`skills` and/or `auto_loaded_skills`).
6. Construct `Agent(...)`.
7. Validate tool chains and run behavior with tests.

## Core Principle: Model Judgment, Code Orchestration

Use the model to choose an entry point or resolve genuine ambiguity. Once an
output determines what must happen next, express that transition as a tool-chain
edge instead of asking the model to choose again.

This division is central to Roboz:

- passive tool schemas and descriptions stay out of the model's active tool surface
- known follow-up steps need no extra model round trip
- typed outputs become validated inputs for the next stage
- ordinary Python predicates make routing explicit and testable

A chain may continue linearly, choose one conditional branch, converge from
several possible parents, yield control back to the model when no edge matches,
or terminate by returning `Stop`. Pair chains with per-message truncation when
intermediate results should be useful briefly or should never enter model context.

See [tool chaining and message lifecycle](tool-authoring.md#tool-chaining) for the
exact graph and context semantics.

## Supporting Principle: Skills First, Prompt Lean

When a behavior can be separated from core agent identity and reused, extract it into a `Skill`.

Practical policy:

- Check existing project and package skills before writing a new one.
- Keep `system_prompt` focused on role, routing policy, and high-level flow.
- Put detailed operational guidance (formats, checklists, decision procedures, CLI usage policy) into skills.
- Load foundational skills with `auto_loaded_skills` so detailed guidance arrives as instructions without bloating the base system prompt.

Why this is a good default:

- improves reusability across agents
- reduces prompt bloat and token pressure
- keeps separation of concerns clean (agent identity vs reusable operating policy)

## Step 1: Prompt Policy First

Keep prompts focused on role, priorities, and decision policy.

Do:

- Describe what the agent is responsible for.
- Describe how it should decide between tools.
- State conflict resolution policy for ambiguous instruction patterns.
- Keep instructions broad and role-centric; delegate detailed playbooks to skills.

Do not:

- Hardcode tool schemas or argument JSON in prompt prose.
- Duplicate tool descriptions that runtime appends automatically.
- Pack large procedural checklists into system prompt when they can live in reusable skills.

Keep the role prompt stable and move evolving procedures into independently testable skills.

## Step 2: Build Tool Surface Deliberately

`Agent` differentiates tool roles:

- **Active tools**: actions the model can select via `Invoke(action=...)`
- **Passive/chained tools**: orchestration steps triggered by previous outputs

In practice, use active tools for explicit external actions (read/write/execute/submit),
and chained tools for deterministic routing and flow control.

For example, command execution is an active choice, while validation that must
always follow a successful command is a good candidate for passive chaining.
If validation produces bulky diagnostics, attach a graded truncation policy to
that output so it remains actionable while fresh and ages out of model context.

## Step 3: Make Constructors Side-Effect Free

Follow this core rule:

- `get_<agent_name>(...)` constructors are configuration/wiring only.
- Do not mutate repositories, files, or state at construction time.
- Runtime side effects should happen through tools (including startup hooks that are tools).

Why this matters:

- Constructors may run repeatedly.
- Side effects during construction break idempotency and make behavior surprising.

Reference: [`reference.md`](reference.md)

## Step 4: Choose Startup and Defaults Explicitly

Default/startup tools should do setup or run preflight concerns that are deterministic and low-risk.

Typical examples:

- log retention cleanup
- startup consistency checks
- lightweight usage telemetry reporting
- first user/task prompt tool

If using non-agentic mode, ensure `default_tools` includes at least one entry.
In agentic mode, runtime defaults can involve `prompt_agent`.

Reference: [`reference.md`](reference.md)

## Step 5: Use Skills for Reusable Policy

Use skills for reusable instructions and optional toolsets. Before creating a new skill, check existing project and package skills; before expanding an agent prompt, ask whether the behavior belongs in `Skill.instructions`.

Load foundational skills with `auto_loaded_skills`. Keep optional or large toolsets available through invocable skill wrappers.

References:

- [`reference.md`](reference.md)
- [`tool-authoring.md`](tool-authoring.md)

## Step 6: Minimal Construction Pattern

```python
import roboz as rz

agent = rz.Agent(
    name="my_agent",
    agent_endpoint=...,             # endpoint or LazyExternalDependency
    tools=[..., rz.stop],           # active + chained
    default_tools=[...],            # startup/default flow
    skills=[...],                   # optional on-demand skills
    auto_loaded_skills=[...],       # optional always-on skills
    system_prompt="...",
)
```

Then verify with:

- chain compatibility checks (automatically validated by runtime)
- representative invoke tests
- any permission boundary tests for side-effect tools

## Dependency Views

Tools are the sole source of truth for external operational dependencies.
`agent.external_dependencies()` derives a deduplicated view from the master,
active, passive, default, prompt-user, skill, and auto-loaded-skill Tool graph.
Dynamic `Agent.add()` calls therefore update the view without a separate agent
registry.

By default the view includes tools from configured but not-yet-loaded skills so
deployment preflight can report potential requirements. Pass
`include_lazy_skills=False` for only the currently composed graph.

A context can inspect a child before a wrapper tool is constructed:

```python
ctx = rz.Ctx(agent=child)
dependencies = ctx.external_dependencies()
subagent_tool = rz.run_subagent(ctx)
```

Subagent and background-agent tools retain their bound context as a live
source. Later `child.add()` calls are visible through `ctx.external_dependencies()`,
the wrapper tool, its copies, and the parent agent's dependency view.

Endpoints must be concrete endpoint objects or `LazyExternalDependency`
instances. Raw callable endpoints are not supported.

Keep catalog endpoints canonical and attach provider request policy at the use
site:

```python
from roboz.llm import with_request_options

planning_endpoint = with_request_options(
    catalog_endpoint,
    extra_body={"reasoning": {"effort": "high"}},
)
```

This returns a distinct endpoint configuration. Lazy catalog dependencies remain
unresolved until the configured endpoint is materialized, while preserving their
dependency ID and redacted metadata.

## Operational Guardrails

- Keep active tool names action-oriented (verb-first).
- Keep chains narrow and explicit; avoid giant fan-in/fan-out graphs without tests.
- Prefer cloning tools (`copy(...)`) when reused in different chain graphs.
- Keep prompt policy stable and evolve tool behavior through tools, not prompt hacks.

## Definition of Done for New Agents

Before considering an agent ready:

1. Constructor is side-effect free.
2. Active/passive/default tool responsibilities are clear.
3. Skill loading policy is intentional (`skills` vs `auto_loaded_skills`).
4. Prompt does not duplicate runtime-appended tool schema details.
5. Tests cover primary flow and failure/guardrail behavior.
