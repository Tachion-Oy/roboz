# Typed contexts and resource inspection

A factory annotates `ctx` with the concrete object it uses. A tool author working
with a model endpoint should eventually write `ctx: LLMEndpoint`; an integration
providing a `Grep` resource should let its tools write `ctx: Grep`. Binding passes
that object directly to the factory's callable and preserves its concrete type.
Resource implementers supply the inspection contract through inheritance.

This checkpoint establishes that binding contract. It does **not** migrate or
introduce endpoint or grep implementations. Agents, built-in tools, Shed,
endpoint definitions and catalogs, model selection, and lazy clients remain
for subsequent checkpoints. The complete library is not release-ready at this
boundary; the primitive imports and examples below work independently.

## The object being injected

`Context` is a structural typing protocol with one method:

```python
from roboz.dependencies import ExternalDependency


def external_dependencies(self) -> tuple[ExternalDependency, ...]:
    ...
```

Factory authors annotate their particular concrete class, rather than widening
it to `Context`. Ordinary contexts do not inherit or instantiate the protocol.
They explicitly report their resources. A configuration-only context returns
`()`. For example:

```python
from dataclasses import dataclass

from roboz import Message, Str, factory
from roboz.dependencies import ExternalDependency


@dataclass(frozen=True, kw_only=True)
class PrefixContext:
    prefix: str = ""

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return ()


@factory
def prefix_value(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    """Prefix the supplied text with the configured label."""
    return Str(value=f"{ctx.prefix}{input.value}")


bound = prefix_value(PrefixContext(prefix="> "))
assert bound(Str(value="hello"), []).value == "> hello"
assert bound.external_dependencies() == ()
```

The inferred type is `Factory[Str, Str, PrefixContext]`. Constructor arguments
and field access remain statically checked, including through parenthesized and
chained factory decorators.

A context requiring several values is an ordinary typed object too:

```python
from dataclasses import dataclass

from roboz.dependencies import ExecutableDependency, ExternalDependency


@dataclass(frozen=True, kw_only=True)
class ProgramContext:
    executable: ExecutableDependency
    prefix: str = ""

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (self.executable,)
```

Its factory uses `ctx: ProgramContext`. Defaults belong in that constructor.
Roboz does not populate fields, copy the context, or traverse its attributes.
Reusing the same context intentionally shares state; constructing a new context
creates whatever fresh state its constructor defines. Use a dataclass
`field(default_factory=list)`, for example, for independent mutable lists.

## The resource implementer's contract

`roboz.dependencies` contains the resource base `ExternalDependency`, the
`ExternalDependencyKind` enum, the existing `ExecutableDependency` implementation,
and `dedupe_external_dependencies`. These remain in their dedicated module and
are not re-exported from the top-level authoring API.

A concrete resource inherits `ExternalDependency` and implements:

- `dependency_id`: a stable, namespace-qualified identity. Equal IDs designate
  the same logical resource.
- `kind`: `ExternalDependencyKind.EXECUTABLE`, `NETWORK_SERVICE`, or
  `MODEL_ENDPOINT`.
- `redacted_metadata()`: safe, secret-free `Mapping[str, str]` metadata.

Incomplete subclasses cannot be instantiated. The inherited
`external_dependencies()` returns `(self,)` without external work, so a complete
resource already satisfies the context protocol. The resource class exposes its
own operations for the factory to call; the base adds no generic resolution or
materialization operation.

`ExecutableDependency` is retained as an existing concrete implementation for
this checkpoint. Its identity and metadata are unchanged. Its explicit
`resolve()` and `require()` operations resolve a program on `PATH`; `require()`
raises `FileNotFoundError` when unavailable. Binding and inspection do not call
either method or start a process.

This complete primitive example demonstrates direct resource binding without an
endpoint or agent:

```python
from roboz import Message, Str, factory
from roboz.dependencies import ExecutableDependency


@factory
def describe_program(
    input: Str,
    messages: list[Message],
    ctx: ExecutableDependency,
) -> Str:
    """Describe the supplied value using the configured program name."""
    return Str(value=f"{ctx.executable}: {input.value}")


program = ExecutableDependency("python")
bound = describe_program(program)
result = bound(Str(value="hello"), [])

assert result.value == "python: hello"
assert bound.external_dependencies() == (program,)
assert bound.external_dependencies()[0] is program
assert bound.copy().external_dependencies()[0] is program
```

Its inferred type is `Factory[Str, Str, ExecutableDependency]`. A later endpoint
checkpoint will apply the same contract to factories annotated `ctx: LLMEndpoint`.

## Binding and live inspection

Binding checks that `external_dependencies` is callable, captures the exact
context in the executable closure, and retains that reference for inspection.
The generated callable annotations and tool input schema exclude `ctx`.
Binding does not invoke inspection or perform external work. The concrete type
is enforced statically; the runtime check validates only the inspection interface.

`tool.external_dependencies()` is the sole tool inspection API:

- A plain tool returns `()`.
- A bound tool queries its retained context on each inspection. Deliberately
  exposed changes in a mutable context appear in later results.
- The result must be a tuple of `ExternalDependency` instances. Wrong containers
  and non-resource entries raise `TypeError`; inspection exceptions propagate.
- Duplicate IDs collapse to their first resource, preserving encounter order
  and object identity. `dedupe_external_dependencies` accepts an iterable and
  applies the same entry validation and deduplication rules.

`Tool.copy()` keeps the same context and callable without inspecting either. It
retains the existing fresh-tool-ID behavior. Tools bound from one factory retain
that factory's ID, including when the factory is bound to different contexts.
Input projection and copying, nested payload types, output-model validation,
chain predicates and parent references, and docstring normalization are unchanged.
These checks cover factory construction and direct tool invocation; agent-level
chain execution requires the later agent migration.

## Removed APIs and migration boundary

This is an intentionally incomplete breaking checkpoint, with no compatibility
aliases or import fallbacks.

- Replace `Ctx(...)` with a concrete resource or a typed context object. Dynamic
  keyword fields and the `Any` attribute fallback are removed. `_prepare_context`,
  required-field/default dictionaries, and factory `_prepare_ctx` hooks are
  removed; concrete constructors own defaults and state.
- Resource implementations inherit `ExternalDependency` directly. The separate
  `ExternalDependencySource`, `ExternalDependencyReference`,
  `ModelEndpointDependency`, and `NetworkServiceDependency` bases are removed.
- `LazyExternalDependency`, `DependencyRoute`, and generic `materialize()` are
  removed. No replacement lazy-loading subsystem is introduced here.
- Checker registration is removed: `DependencyChecker`, `DependencyRegistration`,
  `BoundDependency`, `bind_dependencies`, and `DependencyContractError`.
- Replace both `Tool.dependencies` and property access to
  `Tool.external_dependencies` with the method `tool.external_dependencies()`.
  Tools retain one context reference instead of direct-resource/source tuples.
- Top-level `roboz` exports now contain the existing data model exports,
  `Context`, `Factory`, `Tool`, `factory`, and `tool`. Import resource extension
  primitives from `roboz.dependencies`. Eager imports and re-exports of `Agent`,
  background/subagent helpers, `Skill`, `PromptUser`, interaction tools, and
  stopping tools are removed so normal primitive imports stay independent.

Consumer implementations are untouched. They still refer to removed APIs, and
importing their implementation modules is not a supported migration workaround.
Existing README, quick start, consumer guides, and companion documentation still
refer to those unmigrated consumers. This document describes the checkpoint's
supported contract; later checkpoints must migrate those consumers, their tests,
and usage documentation, and pass the complete release gate before release.

## Focused checks

Independent runtime tests live in `tests/primitives/`, outside the existing
agent/runtime unit fixtures. The new typing cases are
`tests/type_tests/cases/valid/test_dependency_primitives.py` and
`tests/type_tests/cases/expected_failures/test_primitive_*.py`. Negative cases
identify the intended typing error in a comment; a missing import is not evidence
that a context typing contract passed.

Runtime and positive typing checks can be run independently:

```bash
uv run pytest tests/primitives tests/test_docstrings.py --no-cov
uv run pyright --project pyrightconfig.type-tests.json \
  tests/type_tests/cases/valid/test_dependency_primitives.py
```

Check each negative case with the same Pyright project and review its diagnostic
against the stated expectation. The unchanged repository-wide gates also include
unmigrated consumers and must not be weakened for this checkpoint.
