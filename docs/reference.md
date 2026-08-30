# Reference

Concise reference for the main `roboz` building blocks. Use the source files for exact implementation details; keep narrative documentation in `docs/`.

## `Agent` Runtime

Main code: [`../src/roboz/agent/core.py`](../src/roboz/agent/core.py)

`Agent` registers tools and skills, builds the system prompt, runs the invoke loop, and emits run events to explicit event sinks.

Persistence is opt-in: pass a `PersistenceSink` (or `default_event_sinks(data_path=...)`) through `Agent(event_sinks=...)`.

At construction time it:

- validates the agent name
- flattens tools and skill-provided tools
- separates active and passive/chained tools
- validates chain compatibility
- prepares prompt generation and defaults

At invoke time it:

- initializes runtime event piping
- appends system and initial messages
- auto-loads configured skills
- loops through tool selection and execution
- exits on `Stop` or cancellation paths

## Tools and Factories

Main code:

- [`../src/roboz/tooling/core.py`](../src/roboz/tooling/core.py)
- [`../src/roboz/tooling/decorators.py`](../src/roboz/tooling/decorators.py)

Use `@tool` for actions that only need `input` and `messages`.
Use `@factory` when the tool needs runtime context.

Both support:

- `chained_to`
- `chain_condition`
- `copy(...)`
- `chain(...)`
- `rename(...)`

Tool names should be imperative action phrases, for example `stop`, `throw_dice`, or `save_file`.

## Skills

Main code: [`../src/roboz/skill/core.py`](../src/roboz/skill/core.py)

A `Skill` packages reusable instructions and optional tools:

- `name`
- `description`
- `instructions`
- `tools`
- `depends_on`

When used with `Agent`, skill wrappers are visible as invocable actions and concrete skill tools can be loaded lazily. Use `auto_loaded_skills` for foundational instructions every run needs.

## LLM Operations

Main package: [`../src/roboz/llm`](../src/roboz/llm)

The `roboz.llm` facade exposes the operations needed to assemble and host an agent:

- the basic system-prompt builder
- completion and structured-completion helpers
- conversation token estimation and context truncation
- endpoint binding, resource access, and resolution

Prompt fragments used to implement those operations live together in `roboz.llm.prompts`. Companion-facing model, runtime, tool, and persistence contracts are likewise exported from their corresponding package facades rather than requiring private-module imports.

## Standard Library

The distribution includes a deliberately lean `roboz.standard` namespace. It
provides permission and sandbox models, guarded shell-command and patch tools,
and agent-runtime and conversation-summarization tools. Import it explicitly
with `import roboz.standard` or through its individual subpackages.

The provider primitives belong to the LLM layer at `roboz.llm.providers`. Its
provider-neutral `ProviderCatalog` accepts endpoint and model-name factories.
The concrete `roboz.standard.providers.openrouter` implementation owns
OpenRouter's credentials, client setup, route naming, single supported model,
and generated typing surface, providing a small template for integrations
maintained by other packages.

Larger integrations—such as Codex, Proton, Firecrawl, Office/PDF tooling,
timesheets, additional model providers, composite agents, and the Hub UI—are
outside the lean package boundary. Future add-ons can depend on `roboz` without
making those integrations prerequisites for local command or patch workflows.

## Runtime Event Bus

Main package: [`../src/roboz/runtime`](../src/roboz/runtime)

`EventPipe` emits one structured stream per invoke. Built-in consumers include CLI/API output and persistence sinks; external hosts can subscribe to the same stream.

Event classes:

- `RunLifecycleEvent`
- `MessageEvent`

When configured with an explicit data path, persistence writes one run document per invoke under date-partitioned conversation paths.
