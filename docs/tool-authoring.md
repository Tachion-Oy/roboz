# Tool Authoring Guide

This guide describes how to build reliable tools and factories in `roboz`, with emphasis on composability and predictable chaining.

Primary references:

- Core reference: [`reference.md`](reference.md)
- Agent composition guide: [`agent-authoring.md`](agent-authoring.md)
- Docstring conventions: [`docstrings.md`](docstrings.md)

## Tool vs Factory

Use:

- `@tool` for pure actions that need only `input` and `messages`
- `@factory` when tool behavior depends on runtime context/config

Both produce tool objects that can participate in chain graphs.

## Authoring Contract

For both `@tool` and `@factory` callables:

- function args are `input` and `messages` (factory adds `ctx`)
- input model subclasses `Empty`
- outputs resolve to permitted model constituents (`Empty | Invoke | Stop` family)
- docstrings give concise agent-facing instructions under the
  [docstring conventions](docstrings.md)

Start with strong typing early. It prevents chain mismatch issues later.

## Baseline Examples

### Simple tool

```python
import roboz as rz

@rz.tool
def summarize(input: rz.Empty, messages: list[rz.Message]) -> rz.Str:
    """Summarize the available conversation material."""
    return rz.Str(value="done")
```

### Context-aware factory

Use `Ctx` directly; no class definition or inheritance is needed.

```python
import roboz as rz

@rz.factory
def add_prefix(
    input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx
) -> rz.Str:
    """Prefix the supplied text with the configured label."""
    return rz.Str(value=f"{ctx.prefix}{input.value}")

tool_instance = add_prefix(rz.Ctx(prefix="[agent] "))
```

`Ctx` accepts keyword fields and exposes them as attributes. Names must be public
Python identifiers and cannot be keywords or reserved API/implementation names.
`external_dependencies` is reserved; `dependencies` and `values` remain valid fields.
Bindings cannot be reassigned or deleted, but contained objects keep their
identity and may be mutable. A missing field raises `AttributeError`. Contexts
are runtime configuration, outside the model's input schema and prompts.

Tool inputs, outputs, and chaining retain their type contracts. Arbitrary
context fields are dynamic: Pyright does not infer their names or types from
`Ctx(...)`. Validate application-specific values where they are consumed.

See [context migration and built-in fields](context-migration.md) for existing
call sites, built-in defaults, and state ownership.

## External Dependencies

A plain `@tool` has no declared external dependencies. Pass resources directly
in a factory context so the same objects drive execution and inspection:

```python
from subprocess import run

import roboz as rz

@rz.factory
def convert(
    input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx
) -> rz.Str:
    """Run the configured converter on the supplied value."""
    run([ctx.converter.require(), input.value], check=True)
    return input

ctx = rz.Ctx(prefix="[agent]", converter=rz.ExecutableDependency("my-converter"))
dependencies = ctx.external_dependencies()  # Inspect before building a tool.
tool_instance = convert(ctx)
```

`require()` resolves the configured executable using `shutil.which()` and returns
a `pathlib.Path`, or raises `FileNotFoundError`. `subprocess.run()` executes it.
The resulting tool exposes the same executable object through `dependencies`
and `external_dependencies`; authors do not maintain a separate resource list.

Service adapters implement `NetworkServiceDependency`. Bind the service directly
and invoke it through `ctx.service`, for example `ctx.service.search_messages(...)`.
Factories that independently require several resources use several context
fields. Direct tuple entries are also collected. Ordinary configuration values
are ignored; arbitrary nested containers and object attributes are not walked.
Aggregate catalogs and agents expose their current dependencies through
`ExternalDependencySource`. `Ctx` also implements this interface, so contexts
can contain other contexts as live sources.

Every `ExternalDependency` supports `materialize()`. Eager dependencies return
themselves; `LazyExternalDependency` carries inspectable identity and caches its
first successful resolution. Context binding, `Tool.copy()`, and dependency
inspection do not materialize resources, contact services, or execute processes.
Use `ExternalDependencyReference` for routes whose selected resource can change.
The selected dependency owns its identity validation and client cache.

`Ctx.external_dependencies()` returns a `tuple[ExternalDependency, ...]`: direct
resources first, then live-source resources, deduplicated by ID while retaining
the first object. Each inspection reads the current source graphs without
copying resources or running health checkers.

`Tool.dependencies` contains direct resources in field/tuple order, including
repeated resources. `Tool.external_dependencies` adds live source dependencies
from its bound context and deduplicates by `dependency_id`, retaining the first
resource. Copies keep the original callable, resource identities, and captured
state.

## Standalone LLM-backed tools

The public `roboz.llm` operations do not require an `Agent`. Bind an endpoint
object directly and validate its completion against the output model:

```python
import roboz as rz
from roboz.llm import call_llm_api, get_completion

@rz.factory
def summarize_with_llm(
    input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx
) -> rz.Str:
    """Summarize the conversation using the configured model."""
    result = get_completion(
        messages=messages,
        LlmOutputModel=rz.Str,
        call_llm_api=lambda current: call_llm_api(ctx.endpoint, current),
    )
    return rz.Str(**result)

summarize = summarize_with_llm(rz.Ctx(endpoint=endpoint))
```

Supply an `EndpointLike`: a concrete endpoint, an `ExternalDependencyReference`
(including lazy endpoint resources), or a `MockLLMEndpoint` for deterministic tests. Real endpoint resources are discovered
automatically; mocks are ordinary context values and add no external dependency.
The LLM call resolves the endpoint when it is needed.

## Tool Chaining

Tool chaining moves known control flow out of the model loop. The model selects
an active tool; its typed output can then run one passive successor directly.
Because any tool with `chained_to` is passive, its description and schema are not
part of the model's active tool surface.

```text
                                      ┌─ predicate A ─> B ─┐
model ─> active tool A ─> typed output┤                    ├─> D
                                      └─ predicate B ─> C ─┘
```

The graph supports these precise shapes:

- **Linear:** `B(chained_to=A)` always follows `A` when its predicate is true.
- **Conditional fan-out:** several children may name `A` as their parent, but
  exactly zero or one predicate may match each output. This is exclusive routing,
  not parallel broadcast; multiple matches raise `RuntimeError`.
- **Converging fan-in:** `D(chained_to=[B, C])` may follow either parent. It does
  not wait for both parents or aggregate their outputs.

If no child matches, the chain ends and the agent resumes its configured default
flow, normally returning control to the model. Returning `Stop` ends the entire
agent run immediately, so no successor is selected.

### Conditional routing

`chain_condition` receives the parent's concrete output. It can branch on the
output type or value and may close over application policy:

```python
@rz.tool(
    chained_to=inspect_result,
    chain_condition=lambda output: isinstance(output, Approved),
)
def publish(input: Approved, messages: list[rz.Message]) -> rz.Str:
    """Publish an approved result."""
    ...
```

A predicate may also consult injected state, such as a policy object's calendar:

```python
chain_condition=lambda output: output.ready and policy.today().weekday() != 1
```

Keep predicates small and side-effect free. Inject clocks and other changing
state so every branch can be tested deterministically. Put the work itself in the
child tool, not in its predicate.

### Types, identity, and graph safety

Parent outputs and child inputs are checked by the type checker and validated
again when the `Agent` graph is constructed. A union-output parent can route to a
child accepting one constituent when an explicit predicate selects that branch.
Every referenced parent must be present in the same agent graph.

At runtime Roboz requires a unique next tool. Test the no-match, each-match, and
ambiguous-match cases for conditional branches. See
[`examples/tool_chaining.py`](../examples/tool_chaining.py) for a runnable
conditional fan-out that converges on one finalizer.

## Message Lifecycle and Truncation

Every tool output inherits `AgentBaseModel.truncation`. When Roboz turns that
output into a `Message`, the policy follows it. The policy is applied each time a
model request is assembled, based on how many newer messages now follow it; the
stored message itself is not rewritten.

```python
from roboz.models import Severity, Truncation

TRACEBACK_LIFECYCLE = [
    Truncation(threshold=0, severity=Severity.LIGHT),
    Truncation(threshold=3, severity=Severity.STUB),
    Truncation(threshold=8, severity=Severity.REMOVE),
]

return rz.Str(value=traceback, truncation=TRACEBACK_LIFECYCLE)
```

In this policy a short traceback remains readable while fresh (`LIGHT` only caps
oversized string fields), becomes a caller/action stub after three newer
messages, and disappears from model context after eight. When several thresholds
match, the rule with the largest threshold wins.

Use the built-in policies deliberately:

- `DEFAULT` applies `LIGHT` immediately, preventing any single string field from
  entering context without a size bound.
- `NO_TRUNCATION` explicitly keeps the full message at every distance.
- `NO_MESSAGE` removes the message from every model request while runtime events
  and configured persistence retain the full content.
- `ERROR_RETRY` keeps a fresh error lightly bounded, then removes it after five
  newer messages.
- `GRADED` progresses from `LIGHT` to `STUB` to `REMOVE` over a longer window.

System messages are always retained. Threshold distance counts later messages,
not turns, tokens, or elapsed time. See
[`examples/message_truncation.py`](../examples/message_truncation.py) for a
runnable projection of the same message at several distances.

## Active and Passive Responsibilities

When authoring tools, decide whether they should be active or passive in the agent:

- Active tools should represent meaningful agent choices.
- Passive tools should represent deterministic follow-up orchestration.

This boundary keeps prompts and action selection surface lean while preserving automation flow.

For purely internal stages, return outputs with `NO_MESSAGE`. For results that
the model may need on the next decision, prefer a graded lifecycle. Chaining and
truncation solve different halves of the same problem: the first removes
unnecessary model decisions, and the second removes unnecessary model history.

## Reuse and Identity

When reusing a tool in multiple chain graphs:

- Prefer `Tool.copy(...)` to avoid accidental id collisions.
- Avoid mutating one shared instance across unrelated workflows unless shared identity is intentional.

This is especially important in larger orchestration agents with many chained paths.

## Guarding Side Effects

For tools that touch filesystem/repository/CLI:

- declare explicit allow/deny rules
- make precedence (`allow` vs `deny`) explicit
- keep writes scoped to known directories/patterns

Test both allowed and denied paths, and keep permission checks adjacent to the
operation they protect.

## Tool, Skill, and Package Naming

Tool and Skill names are part of the model-facing API. Both must be lowercase
ASCII `snake_case` matching
`[a-z][a-z0-9]*(?:_[a-z0-9]+)*`. Construction, copying, renaming, and direct
assignment validate this shape at runtime.

Tools use imperative, verb-first names such as `stop`, `save_file`, and
`run_command`. The name should state the action rather than a category or an
implementation detail. A tool may dispatch several closely related operations
when its umbrella verb describes the whole contract and an explicit input field
selects the operation. Avoid vague `work_with_*` names and suffixes such as
`_passive`.

Skills use noun-style names describing a domain, concept, or deliverable. Avoid
gerund (`-ing`) tokens and the `_tools` suffix. Prefer singular names; use a
plural only when plurality is important to what the skill teaches or exposes.
The generated tool used to select a skill intentionally has the same noun-style
name as the skill.

Within a Shed skill package, the public module that constructs or exposes tools
is named `tools.py`, or `tools/` when it needs submodules. Do not use a top-level
`tool.py` or `command.py` for that role. Focused implementation modules such as
`resolve.py`, `executor.py`, and `runtime.py` retain their responsibility-based
names.

Grammar and domain meaning cannot be inferred reliably by the generic runtime,
so package-specific CI checks maintain approved verbs, skill names, and plural
exceptions. Keep names stable: changing one is an API and behavior change for
prompts, stored calls, and chains.

## Testing Strategy for Tools

For each non-trivial tool/factory:

1. Unit test input/output behavior.
2. Test chain compatibility where it is wired.
3. Test guard conditions and denial paths for side-effect tools.
4. Test error messages for common misuse (bad input, missing context).

Related test guidance:

- [`build-and-test.md`](build-and-test.md)

## Common Pitfalls

- Overloading one tool with multiple unrelated responsibilities.
- Using chain conditions as hidden control-flow language.
- Treating conditional fan-out as parallel execution or fan-in as a join barrier.
- Allowing two successor predicates to match the same output.
- Reusing one mutable tool instance in different graphs.
- Exposing too many active tools when passive chaining would do.
- Hiding output with `NO_MESSAGE` when the model still needs it for its next decision.
- Prompting around weak tool contracts instead of fixing tool design.
