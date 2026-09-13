# Typed contexts and resource inspection

A factory annotates `ctx` with the concrete object it uses. A tool author working
with a model endpoint writes `ctx: LLMEndpoint`; an integration providing a
`Grep` resource should let its tools write `ctx: Grep`. Binding passes
that object directly to the factory's callable and preserves its concrete type.
Resource implementers supply the inspection contract through inheritance.

The primitive checkpoint establishes that binding contract. Its endpoint
follow-up makes the core `LLMEndpoint` and `TranscriptionEndpoint` directly
bindable too. Agents, built-in tools, Shed, provider construction, catalogs, and
lazy clients remain for subsequent checkpoints. No grep integration is introduced
here. The complete library is not release-ready at this boundary; the primitive
imports and examples below work independently.

## The object being injected

A context can be any value with the type declared by the factory: a resource,
an ordinary class instance, a list, or another value. It need not inherit a
framework class or implement a dependency method. The exact supplied object is
injected; a resource-free context is never replaced with an empty container.

`Context` describes the optional structural inspection interface:

```python
from roboz.dependencies import ExternalDependency


def external_dependencies(self) -> tuple[ExternalDependency, ...]:
    ...
```

Factory authors annotate their particular concrete type. Ordinary contexts do
not inherit or instantiate the protocol. If a context has no inspection method,
its tool reports `()`. Contexts that explicitly report resources can provide the
method above. For example, this configuration needs no inspection boilerplate:

```python
from dataclasses import dataclass

from roboz import Message, Str, factory


@dataclass(frozen=True, kw_only=True)
class PrefixContext:
    prefix: str = ""


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

A list is also a valid typed context. Its element type is checked when binding:

```python
from roboz import Message, Str, factory


@factory()
def remember_value(input: Str, messages: list[Message], ctx: list[str]) -> Str:
    """Record the supplied value and return the recorded values."""
    ctx.append(input.value)
    return Str(value=", ".join(ctx))


history: list[str] = []
bound = remember_value(history)
assert bound(Str(value="hello"), []).value == "hello"
assert history == ["hello"]
assert bound.external_dependencies() == ()
```

The inferred type is `Factory[Str, Str, list[str]]`. Binding a `list[int]` is a
type error. Lists containing resources are not traversed automatically; use a
context with explicit inspection to declare those resources.

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

An external dependency represents a resource whose availability depends on the
surrounding environment and can change independently of our code. Contexts in
general have no such requirement. Inspection reports resources and safe metadata;
it does not establish availability. Call a resource's explicit `check()` method
to check its current availability.

A concrete resource inherits `ExternalDependency` and implements:

- `dependency_id`: a stable, namespace-qualified identity. Equal IDs designate
  the same logical resource.
- `kind`: `ExternalDependencyKind.EXECUTABLE`, `NETWORK_SERVICE`, or
  `MODEL_ENDPOINT`.
- `redacted_metadata()`: safe, secret-free `Mapping[str, str]` metadata.
- `check() -> bool`: an explicit current availability check. Return `False` for
  an absent resource; propagate errors that prevent checking availability.

Incomplete subclasses cannot be instantiated. The inherited
`external_dependencies()` returns `(self,)` without external work, so a complete
resource already satisfies the context protocol. The resource class exposes its
own operations for the factory to call; the base adds no generic resolution or
materialization operation.

`ExecutableDependency` is retained as an existing concrete implementation for
this checkpoint. Its identity and metadata are unchanged. Its explicit
`resolve()` and `require()` operations resolve a program on `PATH`; `require()`
raises `FileNotFoundError` when unavailable. Its `check()` returns whether
`resolve()` finds the program. Binding and inspection do not call these methods
or start a process.

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

Its inferred type is `Factory[Str, Str, ExecutableDependency]`.

## Concrete endpoint contexts

Core chat and transcription endpoints inherit `ExternalDependency` directly.
Their existing model identities, safe metadata, and configuration defaults
remain unchanged. Their clients now have an explicit OpenAI-compatible contract.
Both report `MODEL_ENDPOINT` and inherit inspection
that returns themselves.

This example uses the optional `openai` SDK, available in the repository dev
environment or via `roboz-endpoints[openai]`. It needs no live credentials or
service: the tool only describes the configured model, and the placeholder
client makes no requests:

```python
from openai import OpenAI

from roboz import Message, Str, factory
from roboz.llm import LLMEndpoint


@factory()
def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    """Describe the supplied text using the configured model name."""
    return Str(value=f"{ctx.model_name}: {input.value}")


client = OpenAI(api_key="example-only-key", base_url="https://example.invalid/v1")
endpoint = LLMEndpoint(client=client, api_name="example", model_name="example-model")
bound = describe_model(endpoint)
assert bound(Str(value="hello"), []).value == "example-model: hello"
assert bound.external_dependencies()[0] is endpoint
assert bound.copy().external_dependencies()[0] is endpoint
client.close()
```

The inferred factory type is `Factory[Str, Str, LLMEndpoint]` for both `@factory`
and `@factory()`. Within the callable, Pylance sees the declared endpoint fields
and their types, including the client methods. `LLMEndpoint.client` is typed as
`OpenAICompatibleChatClient`; the transcription client is typed as
`OpenAICompatibleTranscriptionClient`. Both protocols, in
`roboz.llm.openai_compatible`, require synchronous model discovery plus the
relevant chat or audio API. The real synchronous `openai.OpenAI` client satisfies
both without adapters or casts; an async client or a client missing required
operations is a static type error.

These are structural typing contracts for the OpenAI-compatible operations core
actually uses, not a general provider interface. Clients need no inheritance or
runtime wrapper. Core imports no SDK. Pydantic checks the required top-level API
attributes when constructing an endpoint and preserves the supplied client;
static checking covers nested methods and signatures. It does not make requests
or recursively validate the implementation. The client remains opaque in JSON
schemas and retains its existing Python serialization behavior.

Chat request controls and consumed completion/transcription fields are typed.
The existing extensible message payload boundary remains permissive. Model
listings return `object` at the client boundary because checks validate their
contents; optional response diagnostics and stream chunks are inspected
separately. Different provider APIs need their own explicitly supported
implementation.

For a model call, supply an already configured client to `LLMEndpoint` and pass
`ctx` directly to `call_llm_api(ctx, messages)`. The existing `get_completion`
helper can validate the completion against an output model. Neither helper
requires an agent. Likewise, a transcription factory can annotate
`ctx: TranscriptionEndpoint` and call `call_transcription_api(ctx, ...)`.
Constructing clients is the caller's responsibility; no lazy construction is
added at this step.

`resolve_endpoint` and the transcription validator return the supplied endpoint
unchanged, rejecting other resources and the wrong endpoint family. They no
longer materialize references. `EndpointLike` contains only `LLMEndpoint` and
`MockLLMEndpoint`; `TranscriptionEndpointLike` contains the corresponding
transcription pair. The scripted mock classes satisfy `Context` by reporting
`()`. A factory accepting both real and scripted chat endpoints can annotate
`ctx: EndpointLike`; a factory annotated `ctx: LLMEndpoint` requires that concrete
class, which can be tested using a scripted client.

`with_request_options` and `with_openrouter_policy` accept concrete chat
endpoints. Each returns an endpoint copy with detached, validated request options
while retaining its client and dependency identity. Existing protections against
overriding framework-owned request fields remain in place.

The selector defined alongside the endpoint types now holds concrete
`LLMEndpoint` objects instead of deleted lazy wrappers. Its selection and locking
behavior is unchanged; catalog integration and live routing remain deferred.

## Explicit availability checks

Availability checking is a resource operation. Plain contexts, including lists,
have no check requirement. Integration authors must implement `check() -> bool`
when they inherit `ExternalDependency`; a subclass missing it is abstract.

Each explicit call observes current availability without caching a result.
`False` means the resource was absent in that observation. Authentication,
connection, timeout, and malformed-response errors propagate to the caller.
Checking is never triggered by dependency inspection, factory binding, or
`Tool.copy()`; those operations remain safe before external resources are ready.

For example, checking this interpreter's executable does not start a process:

```python
import sys

from roboz.dependencies import ExecutableDependency

program = ExecutableDependency(sys.executable)
assert program.check() is True
```

Chat and transcription endpoints share an explicitly OpenAI-compatible check
function. They check their configured client's model-discovery
API, `client.models.list(timeout=10.0)`, and confirm the model appears in the
returned `data`. Model entries can be objects or dictionaries with string `id`
values. The existing recognition of canonical model names before a route suffix
is preserved. The check generates no completion or transcription and does not
replace or construct the client.

A successful endpoint check confirms the discovery request succeeded and the
model was listed. A subsequent model call can still fail; availability is an
observation at the time of the check. Credential loading and lazy client
construction remain for a later checkpoint. Check scheduling, retries, cached
health status, and deployment readiness policy belong to consumers and are not
introduced by this method.

## Binding and live inspection

Binding accepts any concrete context type. If `external_dependencies` is
present, it must be callable. Binding captures the exact context in the executable
closure and retains that reference for optional inspection.
The generated callable annotations and tool input schema exclude `ctx`.
Binding does not invoke inspection or perform external work. The concrete type
is enforced statically; runtime binding only checks the optional inspection
method when present.

`tool.external_dependencies()` is the sole tool inspection API:

- A plain tool, or one bound to a context without inspection, returns `()`.
- When provided, a bound tool queries its context's inspection method on each
  inspection. Deliberately
  exposed changes in a mutable context appear in later results.
- An inspection method's result must be a tuple of `ExternalDependency`
  instances. This return contract is independent of the context's own type;
  a list is a valid context. Wrong return containers
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

Agent, built-in tool, and companion implementations are untouched. They still
refer to removed APIs, and importing their implementation modules is not a
supported migration workaround. Existing README, quick start, consumer guides,
and companion documentation still refer to those unmigrated consumers. This document describes the checkpoint's
supported contract; later checkpoints must migrate those consumers, their tests,
and usage documentation, and pass the complete release gate before release.

At this checkpoint, isolated consumer import checks stop at these removed names:

| Consumer import | First missing import |
| --- | --- |
| `roboz.agent`, `roboz.tools`, `roboshed.dependency_health` | `Ctx` from `roboz.tooling.context` |
| `roboshed.deployments`, `roboz_proton_bridge` | `Ctx` from `roboz` |
| `roboz_endpoints` | `ExternalDependencyKind` from `roboz` |

The bare `roboshed` package imports, but that does not validate its deferred
implementations. These first failures can conceal additional removed-API uses.

## Focused checks

Independent runtime tests live in `tests/primitives/`, outside the existing
agent/runtime unit fixtures. The new typing cases are
`tests/type_tests/cases/valid/test_dependency_primitives.py`,
`tests/type_tests/cases/valid/test_endpoint_primitives.py`,
`tests/type_tests/cases/valid/test_plain_contexts.py`,
`tests/type_tests/cases/valid/test_external_checks.py`,
`tests/type_tests/cases/valid/test_openai_compatible_clients.py`, and
`tests/type_tests/cases/expected_failures/test_primitive_*.py`. Negative cases
identify the intended typing error in a comment; a missing import is not evidence
that a context typing contract passed.

Runtime and positive typing checks can be run independently:

```bash
uv run pytest tests/primitives tests/test_docstrings.py --no-cov
uv run pyright --project pyrightconfig.type-tests.json \
  tests/type_tests/cases/valid/test_dependency_primitives.py \
  tests/type_tests/cases/valid/test_endpoint_primitives.py \
  tests/type_tests/cases/valid/test_plain_contexts.py \
  tests/type_tests/cases/valid/test_external_checks.py \
  tests/type_tests/cases/valid/test_openai_compatible_clients.py
```

Check each negative case with the same Pyright project and review its diagnostic
against the stated expectation. The unchanged repository-wide gates also include
unmigrated consumers and must not be weakened for this checkpoint.
