from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from roboz import (
    Agent,
    Ctx,
    Empty,
    ExternalDependency,
    ExternalDependencyKind,
    ExternalDependencyReference,
    LazyExternalDependency,
    Message,
    Str,
    factory,
)
from roboz.llm import (
    LLMEndpoint,
    TranscriptionEndpoint,
    resolve_endpoint,
    with_openrouter_policy,
    with_request_options,
)
from roboz.llm.binding import resolve_transcription_endpoint


class Reference[T: ExternalDependency](ExternalDependencyReference[T]):
    def __init__(self, getter: Callable[[], LazyExternalDependency[T]]) -> None:
        self.getter = getter

    def materialize(self) -> T:
        return self.getter().materialize()

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (self.getter(),)


@factory
def selected_model(input: Empty, messages: list[Message], ctx: Ctx) -> Str:
    return Str(value=resolve_endpoint(ctx.endpoint).model_name)


@pytest.mark.parametrize("policy", ["none", "options", "openrouter"])
def test_reference_switches_through_context_tools_and_agent(policy: str) -> None:
    constructions = []
    requests = []

    def lazy(name: str) -> LazyExternalDependency[LLMEndpoint]:
        def create(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"action":"stop","rationale":"done","value":"ok"}'
                        )
                    )
                ],
                usage=None,
            )

        def construct():
            constructions.append(name)
            return LLMEndpoint(
                client=SimpleNamespace(
                    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
                ),
                api_name="test",
                model_name=name,
                stream=False,
            )

        return LazyExternalDependency(
            f"model:test:{name}", ExternalDependencyKind.MODEL_ENDPOINT, {}, construct
        )

    first, second = lazy("first"), lazy("second")
    selected = first
    reference = Reference(lambda: selected)
    endpoint = reference
    if policy == "options":
        body = {"reasoning": {"effort": "low"}}
        endpoint = with_request_options(reference, extra_body=body)
        body["reasoning"]["effort"] = "high"
    elif policy == "openrouter":
        endpoint = with_openrouter_policy(reference, reasoning_effort="low")
    ctx = Ctx(endpoint=endpoint, duplicate=(endpoint,))
    bound = selected_model(ctx)
    copied = bound.copy()
    from roboz.tools import stop

    agent = Agent(
        name="reference_agent",
        system_prompt="Stop.",
        tools=[stop],
        agent_endpoint=endpoint,
    )
    assert constructions == []
    assert not isinstance(reference, ExternalDependency)
    with pytest.raises(AttributeError, match="immutable"):
        ctx.endpoint = first
    clients = []
    for selected in (first, second, first):
        previous = list(constructions)
        for discovered in (
            ctx.external_dependencies(),
            bound.external_dependencies,
            copied.external_dependencies,
            agent.external_dependencies(),
        ):
            assert discovered == (selected,)
        assert selected.external_dependencies() == (selected,)
        assert constructions == previous
        assert bound(Empty(), []).value == selected.materialize().model_name
        assert copied(Empty(), []).value == selected.materialize().model_name
        materialized = resolve_endpoint(endpoint)
        clients.append(materialized.client)
        if policy != "none":
            assert materialized.extra_body["reasoning"] == {"effort": "low"}
            materialized.extra_body["reasoning"] = {"effort": "max"}
            assert resolve_endpoint(endpoint).extra_body["reasoning"] == {
                "effort": "low"
            }
        agent.master_tool(Empty(), [Message(role="user", content="Stop.")])
        assert requests[-1]["model"] == selected.materialize().model_name
    assert constructions == ["first", "second"]
    assert clients[0] is clients[2]
    assert clients[0] is not clients[1]
    # A direct resource still takes precedence when a reference discovers its ID.
    assert Ctx(reference=reference, direct=first).external_dependencies() == (first,)


@pytest.mark.parametrize("mismatch", ["id", "kind"])
def test_selected_lazy_validates_and_retries_failed_resolution(mismatch: str) -> None:
    good = LLMEndpoint(client=object(), api_name="test", model_name="first")
    bad = (
        LLMEndpoint(client=object(), api_name="test", model_name="wrong")
        if mismatch == "id"
        else LazyExternalDependency(
            good.dependency_id, ExternalDependencyKind.NETWORK_SERVICE, {}, lambda: good
        )
    )
    results = iter([bad, bad, good])
    selected = LazyExternalDependency(
        good.dependency_id, good.kind, {}, lambda: next(results)
    )
    reference = with_request_options(Reference(lambda: selected), extra_body={})
    for _ in range(2):
        with pytest.raises(ValueError, match=f"different dependency {mismatch}"):
            reference.materialize()
    assert reference.materialize().client is good.client
    assert reference.materialize().client is good.client


def test_transcription_reference_and_invalid_materialized_endpoints() -> None:
    transcript = TranscriptionEndpoint(
        client=object(), api_name="test", model_name="speech"
    )
    lazy = LazyExternalDependency(
        transcript.dependency_id, transcript.kind, {}, lambda: transcript
    )
    reference = Reference(lambda: lazy)
    assert resolve_transcription_endpoint(reference) is transcript
    with pytest.raises(TypeError, match="LLMEndpoint"):
        resolve_endpoint(cast(Any, reference))
    with pytest.raises(TypeError, match="LLMEndpoint"):
        with_request_options(cast(Any, reference), extra_body={}).materialize()
    llm = LLMEndpoint(client=object(), api_name="test", model_name="chat")
    with pytest.raises(TypeError, match="TranscriptionEndpoint"):
        resolve_transcription_endpoint(
            cast(
                Any,
                Reference(
                    lambda: LazyExternalDependency(
                        llm.dependency_id, llm.kind, {}, lambda: llm
                    )
                ),
            )
        )


def test_switch_during_provider_call_keeps_inflight_endpoint() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from roboz.llm.calls import call_llm_api

    entered, release = Event(), Event()
    requests = []

    def create(**request):
        requests.append(request["model"])
        if request["model"] == "first":
            entered.set()
            assert release.wait(5)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=request["model"]))
            ],
            usage=None,
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    def lazy(name):
        endpoint = LLMEndpoint(
            client=client, api_name="test", model_name=name, stream=False
        )
        return LazyExternalDependency(
            endpoint.dependency_id, endpoint.kind, {}, lambda: endpoint
        )

    first, second = lazy("first"), lazy("second")
    selected = first
    reference = with_request_options(Reference(lambda: selected), extra_body={})
    with ThreadPoolExecutor() as pool:
        inflight = pool.submit(
            call_llm_api, reference, [Message(role="user", content="Hi")]
        )
        try:
            assert entered.wait(5)
            selected = second
            assert reference.external_dependencies() == (second,)
        finally:
            release.set()
        assert inflight.result(timeout=5)[0] == "first"
    assert call_llm_api(reference, [Message(role="user", content="Hi")])[0] == "second"
    assert requests == ["first", "second"]
