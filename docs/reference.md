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

After a tool returns, the runtime selects at most one passive successor whose
`chained_to` edge names that tool and whose `chain_condition` accepts the output.
No match resumes the configured default flow; multiple matches are an ambiguous
graph and raise `RuntimeError`. Returning `Stop` exits before another edge is
selected.

## Tools and Factories

Main code:

- [`../src/roboz/tooling/core.py`](../src/roboz/tooling/core.py)
- [`../src/roboz/tooling/decorators.py`](../src/roboz/tooling/decorators.py)

Use `@tool` for actions that only need `input` and `messages`.
Use `@factory` when the tool needs runtime context: annotate `ctx` with the exact
type and bind the corresponding object. Resource-bearing aggregate contexts can
implement `HasExternalDependencies`; tools inspect that method at inspection time
and deduplicate the returned resources by ID.
See [tool authoring](tool-authoring.md) and
[context migration](context-migration.md).

Both support:

- `chained_to`
- `chain_condition`
- `copy(...)`
- `chain(...)`
- `rename(...)`

`chained_to=[parent_a, parent_b]` means that the child may follow either parent;
it is convergence, not a barrier that waits for both. Several conditional
children may share one parent for exclusive routing, but the runtime does not
broadcast an output to several children.

Tool names should be imperative action phrases, for example `stop`, `throw_dice`, or `save_file`.

## Message Context and Truncation

Main code:

- [`../src/roboz/models/truncation.py`](../src/roboz/models/truncation.py)
- [`../src/roboz/llm/_truncation.py`](../src/roboz/llm/_truncation.py)

`AgentBaseModel` outputs and `Message` objects carry a `TruncationSpec`: either
one `Truncation` rule or a list of rules. A rule has:

- `threshold`: the minimum distance from the newest message at which it applies;
  negative thresholds never apply
- `severity`: `LIGHT`, `STUB`, or `REMOVE`

For every non-system message, `get_truncated_messages_for_context()` computes
`distance = number of later messages`, chooses the applicable rule with the
largest threshold, and creates the model-visible projection:

| Severity | Model-visible result |
| --- | --- |
| `LIGHT` | Preserve structure and cap each string field at `LIGHT_MAX_CHARS`. |
| `STUB` | Keep a placeholder and, when present, the caller and action. |
| `REMOVE` | Omit the message from the request. |

System messages bypass truncation. The projection is prepared for each model
request and does not mutate the conversation, emitted events, or persisted
content.

Public policies are exported from `roboz.models`: `DEFAULT`, `NO_TRUNCATION`,
`NO_MESSAGE`, `ERROR_RETRY`, and `GRADED`. The projection and token-estimation
helpers are exported from `roboz.llm`.

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

The optional `roboz-endpoints[openai]` companion supplies the model catalogue;
see its [endpoint guide](../packages/endpoints/README.md).

For OpenRouter, keep catalog model names canonical (for example
`z-ai/glm-5.3`, never `z-ai/glm-5.3:nitro`) and attach routing policy where the
endpoint is composed:

```python
from roboz.llm import with_openrouter_policy
from roboz_endpoints import openrouter

canonical_endpoint = openrouter.z_ai__glm_5_3

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

The `roboz` package owns typed agent, tool, skill, control, interaction, event,
and persistence primitives. `roboz.deployment` defines generic construction and
capability contracts. `roboshed` applies those primitives:
orchestrator/Librarian composition, workspace structure, concrete capabilities,
memory maintenance, and guarded tools. Provider
adapters live in separate companions. Core never imports its consumers; having
no provider dependency does not make a composition a primitive.

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
