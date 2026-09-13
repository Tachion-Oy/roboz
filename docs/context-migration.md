# Concrete context migration

Factories now preserve the exact type of the object they bind. Replace dynamic
`Ctx` values with an existing resource, a plain configuration value, or an
ordinary typed class. There is no context base class to instantiate.

```python
from dataclasses import dataclass

import roboz as rz


@dataclass(frozen=True, kw_only=True)
class PrefixContext:
    prefix: str


@rz.factory
def add_prefix(
    input: rz.Str,
    messages: list[rz.Message],
    ctx: PrefixContext,
) -> rz.Str:
    """Prefix the supplied text with the configured label."""
    return rz.Str(value=f"{ctx.prefix}{input.value}")


tool = add_prefix(PrefixContext(prefix="[agent] "))
```

Pyright and Pylance retain `Factory[Str, Str, PrefixContext]`, check every
constructor field, and reject binding an unrelated context. Factory binding
captures the exact object and excludes `ctx` from the model-facing input schema.
Copies and repeated bindings retain that same object; construct another context
when independent state is required.

Contexts may also be simple values. Core interaction factories bind strings,
`run_subagent` binds an `Agent`, and a configuration-only factory may bind a
list, mapping, or another application type. Such contexts report no resources.

## Resource-bearing contexts

An `ExternalDependency` is a resource supplied by the runtime environment. It
owns a stable namespace-qualified identity, category, safe metadata, and its
explicit availability check:

```python
from roboz.dependencies import ExecutableDependency


@rz.factory
def convert(
    input: rz.Str,
    messages: list[rz.Message],
    ctx: ExecutableDependency,
) -> rz.Str:
    """Run the configured converter on the supplied value."""
    return input


converter = ExecutableDependency("my-converter")
bound = convert(converter)
assert bound.external_dependencies() == (converter,)
```

Aggregate contexts explicitly implement `external_dependencies()`. Inheriting
`HasExternalDependencies` is optional, but makes a missing implementation an
abstract-class and static typing error. Structural implementations work too.

```python
from dataclasses import dataclass

from roboz import HasExternalDependencies
from roboz.dependencies import ExternalDependency


@dataclass(frozen=True, kw_only=True)
class ProgramContext(HasExternalDependencies):
    executable: ExecutableDependency
    prefix: str = ""

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (self.executable,)
```

There is no automatic traversal of fields, containers, or arbitrary objects.
A configuration-only aggregate simply omits the method or returns `()`.
`Tool.external_dependencies()` queries the retained context at inspection time,
validates a tuple of `ExternalDependency` instances, and deduplicates by
`dependency_id` while preserving the first object and encounter order. Binding
and copying never inspect, resolve, check, or initialize a resource.

Each resource owns synchronous `check() -> bool`. Health consumers may combine
agent resources with standalone resources such as every selectable model. The
former checker registration and callback inspection APIs are removed.

## Endpoint initialization and selection

Endpoint catalogues return concrete `LLMEndpoint` or `TranscriptionEndpoint`
objects. Their clients initialize lazily when a bound factory is invoked; call
`endpoint.materialize()` only when initialization is deliberately required
earlier. Inspection never initializes or checks a client.

Use `LLMEndpointRoute` when a tool or agent should follow live model selection:

```python
from roboz.llm import LLMEndpoint, LLMEndpointRoute

selected: LLMEndpoint = endpoint
route = LLMEndpointRoute(lambda: selected)
```

The getter is typed to return a concrete chat endpoint. A route sits above the
endpoint: it owns no client or separate dependency identity. Inspection reports
the current endpoint, and each model operation resolves the selection once.
Changing the selection affects later operations while an in-flight call retains
the endpoint it already resolved. Give fixed-model tools a concrete endpoint and
selectable tools a route. Request options and OpenRouter policy can be applied to
either a concrete `LLMEndpoint` or `LLMEndpointRoute[LLMEndpoint]`.

A factory annotated `ctx: LLMEndpoint` accepts only a concrete endpoint. Use
`ctx: EndpointLike` when the factory deliberately supports concrete and routed
chat endpoints.

## Built-in contexts

Core exports `BackgroundAgentContext` and `PromptAgentContext` from
`roboz.agent`. The former owns fresh background state by default; the latter
reports and initializes its prompt endpoint. `run_subagent` binds its `Agent`
directly.

Shed centralizes its typed contexts in `roboshed.tools.contexts` and re-exports
them from `roboshed.tools`: `GuardContext`, `FileCommandResolverContext`,
`FileCommandExecutionContext`, `CompactionContext`,
`SnapshotConversationsContext`, `ConsolidateMemoryContext`,
`PurgeFilesContext`, `SleepBetweenRunsContext`, and `EmailContext`. Their
constructors own defaults and fresh state. High-level builders retain their
keyword arguments and construct these contexts internally.

## Removed APIs

Remove imports and uses of:

- `Ctx`, dynamic keyword construction, `_prepare_context`, and `_prepare_ctx`;
- `FactoryCtx`, specialized legacy context names, and `ToolDependency`;
- `ExternalDependencySource`, `ExternalDependencyReference`,
  `ModelEndpointDependency`, and `NetworkServiceDependency`;
- `LazyExternalDependency`, `DependencyRoute`, and generic `materialize()` on
  dependencies;
- `Tool.dependencies` and property access to `Tool.external_dependencies`;
- dependency checker registration and callback inspection helpers.

Use `tool.external_dependencies()` and `agent.external_dependencies()` as
methods. Import resource primitives from `roboz.dependencies`, endpoint types
from `roboz.llm`, and Shed health monitoring from
`roboshed.dependency_health`.
