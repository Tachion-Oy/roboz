# Tool Authoring Guide

This guide describes how to build reliable tools and factories in `roboz`, with emphasis on composability and predictable chaining.

Primary references:

- Core reference: [`reference.md`](reference.md)
- Agent composition guide: [`agent-authoring.md`](agent-authoring.md)

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

Start with strong typing early. It prevents chain mismatch issues later.

## Baseline Examples

### Simple tool

```python
import roboz as rz

@rz.tool
def summarize(input: rz.Empty, messages: list[rz.Message]) -> rz.Str:
    return rz.Str(value="done")
```

### Context-aware factory

```python
from dataclasses import dataclass

import roboz as rz

@dataclass(frozen=True)
class PrefixCtx(rz.FactoryCtx):
    prefix: str

@rz.factory
def summarize_with_prefix(
    input: rz.Empty, messages: list[rz.Message], ctx: PrefixCtx
) -> rz.Str:
    return rz.Str(value=f"{ctx.prefix} done")

tool_instance = summarize_with_prefix(PrefixCtx(prefix="[agent]"))
```

Every factory context must be a frozen `FactoryCtx` dataclass. Dictionaries,
`TypedDict`s, and arbitrary context objects are intentionally rejected.

## External Dependencies

A plain `@tool` has no declared external dependencies. If an operation calls a
model or network service, or starts an executable, make it a factory and inject
that resource through a direct `ToolDependency` context field:

```python
from dataclasses import dataclass
from subprocess import run

import roboz as rz

@dataclass(frozen=True)
class ConvertCtx(rz.FactoryCtx):
    converter: rz.ToolDependency[rz.ExecutableDependency]

@rz.factory
def convert(
    input: rz.Str, messages: list[rz.Message], ctx: ConvertCtx
) -> rz.Str:
    executable = ctx.converter.resource.require()
    run([executable, input.value], check=True)
    return input
```

The binding used by the closure is the declaration. Do not maintain a parallel
list of command names or dependency strings. `Tool.copy()` preserves the exact
bindings captured by the copied closure.

Network adapters use the same contract: implement
`NetworkServiceDependency`, bind the adapter on the execute-stage context, and
invoke `binding.resource`. A factory that independently requires several
resources uses one binding field per resource. If operations have different
requirements, split them into separate Tools instead of adding conditional
dependency metadata.

Every `ExternalDependency` supports `materialize()`. Eager dependencies return
themselves; `LazyExternalDependency` carries stable, redacted identity and
caches construction on first materialization. Inspecting `Tool.dependencies`,
`Tool.external_dependencies`, or an agent dependency view never materializes a
dependency, performs network I/O, or starts a process.

## Standalone LLM-backed tools

The public `roboz.llm` operations do not require an `Agent`. Bind an endpoint in a
factory context, call it, and validate the completion against the tool's output
model:

```python
from dataclasses import dataclass

import roboz as rz
from roboz.llm import (
    EndpointBinding,
    bind_endpoint,
    call_llm_api,
    endpoint_resource,
    get_completion,
)

@dataclass(frozen=True)
class SummarizeCtx(rz.FactoryCtx):
    endpoint: EndpointBinding

@rz.factory
def summarize_with_llm(
    input: rz.Str, messages: list[rz.Message], ctx: SummarizeCtx
) -> rz.Str:
    result = get_completion(
        messages=messages,
        LlmOutputModel=rz.Str,
        call_llm_api=lambda current: call_llm_api(
            endpoint_resource(ctx.endpoint), current
        ),
    )
    return rz.Str(**result)

summarize = summarize_with_llm(SummarizeCtx(bind_endpoint(endpoint)))
```

`MockLLMEndpoint` follows the same public path for deterministic tests. Real
endpoints become `ToolDependency` bindings, so dependency identity and
materialization remain inspectable on the resulting tool.

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
