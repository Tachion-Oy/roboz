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
Use `@factory` when the tool needs runtime context: annotate `ctx: rz.Ctx` and
bind with `factory_instance(rz.Ctx(prefix="hello"))`. Inspect a context before
binding with `ctx.external_dependencies()`, which returns direct and live-source
resources deduplicated by ID. Resources go directly into context fields.
See [tool authoring](tool-authoring.md) and
[context migration](context-migration.md).

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
- per-use endpoint request options through `with_request_options`

`with_request_options(endpoint, extra_body=...)` creates an independent endpoint
configuration for provider-specific JSON options such as routing or reasoning
effort. A lazy endpoint remains lazy and keeps its canonical dependency identity.
The options are defensively copied, are excluded from endpoint serialization and
redacted metadata, and replace any options already attached to that endpoint copy.

For OpenRouter, keep catalog model names canonical (for example
`z-ai/glm-5.3`, never `z-ai/glm-5.3:nitro`) and attach routing policy where the
endpoint is composed:

```python
from roboz.llm import with_openrouter_policy

orchestrator_endpoint = with_openrouter_policy(
    canonical_endpoint,
    reasoning_effort="low",
)
memory_endpoint = with_openrouter_policy(
    canonical_endpoint,
    reasoning_effort="high",
)
planner_endpoint = with_openrouter_policy(canonical_endpoint)
```

The helper sends OpenRouter's throughput sort with required-parameter routing,
the request-level equivalent of the former `:nitro` model suffix. An optional
provider ignore list can be supplied with `ignored_providers=`.

The framework rejects request keys it owns (`messages`, `model`,
`response_format`, `stream`, `stream_options`, and `temperature`). Provider
options must be finite, JSON-compatible values. Keep credentials and other secrets
out of these options even though Roboz does not serialize them.

Prompt fragments used to implement those operations live together in `roboz.llm.prompts`. Companion-facing model, runtime, tool, and persistence contracts are likewise exported from their corresponding package facades rather than requiring private-module imports.

## Companion Packages

The `roboz` package includes its typed primitives and dependency-free reference
tools, including the Librarian memory pipeline. Optional companion distributions
depend on `roboz` and provide integrations that require provider SDKs, guarded
system tools, or application-specific services; Roboz never imports those
companions.

## Runtime Event Bus

Main package: [`../src/roboz/runtime`](../src/roboz/runtime)

`EventPipe` emits one structured stream per invoke. Built-in consumers include CLI/API output and persistence sinks; external hosts can subscribe to the same stream.

The `roboz.runtime` facade also exposes `log_with_data` and
`LOG_DATA_ATTRIBUTE`. The helper writes a self-contained human message and places
optional scalar metadata on the log record under the `roboz_data` attribute so a
host can render concise console logs and structured technical logs independently.
It forwards caller-supplied scalar metadata and `exc_info` to Python logging, so
callers remain responsible for the sensitivity of those values. Roboz's built-in
tool observer and LLM diagnostics emit operational identifiers, types, counts,
durations, and statuses; they do not include provider bodies, exception messages
or tracebacks, prompts, results, or reasoning text.

Event classes:

- `RunLifecycleEvent`
- `MessageEvent`

When configured with an explicit data path, persistence writes one run document per invoke under date-partitioned conversation paths.
