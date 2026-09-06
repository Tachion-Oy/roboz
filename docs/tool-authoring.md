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
Custom resource implementations retain control over `materialize()`, including
endpoint routes that select a different resource for subsequent calls.

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

Supply an `EndpointLike`: a concrete endpoint, a lazy endpoint resource, or a
`MockLLMEndpoint` for deterministic tests. Real endpoint resources are discovered
automatically; mocks are ordinary context values and add no external dependency.
The LLM call resolves the endpoint when it is needed.

## Chaining Patterns

Supported shapes:

- linear: `A -> B`
- consolidation: `[A, B] -> C`
- conditional fork: `A -> B` only when predicate matches output

Use `chain_condition` to encode branching policy.
If omitted, chaining defaults to always-on.

Guidance:

- keep predicates deterministic and simple
- do not bury business logic inside chain predicates
- test branch edges explicitly

Reference chain docs: [`reference.md`](reference.md)

## Active and Passive Responsibilities

When authoring tools, decide whether they should be active or passive in the agent:

- Active tools should represent meaningful agent choices.
- Passive tools should represent deterministic follow-up orchestration.

This boundary keeps prompts and action selection surface lean while preserving automation flow.

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
- Reusing one mutable tool instance in different graphs.
- Exposing too many active tools when passive chaining would do.
- Prompting around weak tool contracts instead of fixing tool design.
