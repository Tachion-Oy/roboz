"""Live model selection retains concrete resources and in-flight requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from typing import Any, cast

import pytest

from roboz import Agent, factory
from roboz.models import Empty, Message, Str
from roboz.tools import stop
from roboz.dependencies import ExternalDependency
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    LLMEndpointRoute,
    MockLLMEndpoint,
    TranscriptionEndpoint,
    call_llm_api,
    resolve_endpoint,
    with_openrouter_policy,
    with_request_options,
)
from roboz.llm.binding import resolve_transcription_endpoint
from roboz.runtime.events import RunLifecycleEvent


@factory
def selected_model(input: Empty, messages: list[Message], ctx: EndpointLike) -> Str:
    return Str(value=resolve_endpoint(ctx).model_name)


def _endpoint(name, constructions, requests):
    initialized = False

    def materialize():
        nonlocal initialized
        if not initialized:
            initialized = True
            constructions.append(name)
        return client

    def create(**request):
        materialize()
        requests.append(request)
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

    def forbidden(**kwargs):
        pytest.fail("No model discovery or client closing was requested")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        models=SimpleNamespace(list=forbidden),
        close=forbidden,
        materialize=materialize,
    )
    return LLMEndpoint(
        client=client,
        api_name=f"api-{name}",
        model_name=name,
        max_context_tokens=100_000 if name == "first" else 200_000,
        temperature=0.2 if name == "first" else 0.8,
        output_format="text" if name == "first" else "json",
        stream=False,
    )


@pytest.mark.parametrize("policy", ["none", "options", "openrouter"])
def test_reference_switches_through_context_tools_and_agent(policy):
    constructions, requests, events = [], [], []
    first, second = [
        _endpoint(name, constructions, requests) for name in ("first", "second")
    ]
    selected = first
    route = LLMEndpointRoute(lambda: selected)
    if policy == "options":
        body = {"reasoning": {"effort": "low"}}
        route = with_request_options(route, extra_body=body)
        body["reasoning"]["effort"] = "high"
    elif policy == "openrouter":
        route = with_openrouter_policy(route, reasoning_effort="low")
    bound = selected_model(route)
    copied = bound.copy()
    fixed = selected_model(first)
    agent = Agent(
        name="reference_agent",
        system_prompt="Stop.",
        tools=[stop],
        agent_endpoint=route,
        event_sinks=[events.append],
    )
    assert constructions == []
    assert not isinstance(route, ExternalDependency)
    clients = []
    for selected in (first, second, first):
        previous = list(constructions)
        for discovered in (
            route.external_dependencies(),
            bound.external_dependencies(),
            copied.external_dependencies(),
            agent.external_dependencies(),
        ):
            assert discovered == (selected,)
            assert discovered[0] is selected
        assert fixed.external_dependencies()[0] is first
        assert constructions == previous
        assert (
            bound(Empty(), []).value == copied(Empty(), []).value == selected.model_name
        )
        resolved = resolve_endpoint(route)
        clients.append(resolved.client)
        if policy != "none":
            assert resolved.extra_body["reasoning"] == {"effort": "low"}
            resolved.extra_body["reasoning"] = {"effort": "max"}
            assert resolve_endpoint(route).extra_body["reasoning"] == {"effort": "low"}
            assert selected.extra_body is None
        events.clear()
        agent.invoke()
        assert requests[-1]["model"] == selected.model_name
        assert requests[-1]["temperature"] == selected.temperature
        lifecycle = [event for event in events if isinstance(event, RunLifecycleEvent)]
        assert [event.kind for event in lifecycle] == ["started", "stopped"]
        for event in lifecycle:
            for name in (
                "api_name",
                "model_name",
                "max_context_tokens",
                "temperature",
                "output_format",
            ):
                assert getattr(event, name) == getattr(selected, name)
    assert constructions == ["first", "second"]
    assert clients[0] is clients[2] and clients[0] is not clients[1]


def test_binding_and_policy_do_not_call_getter_and_invalid_selection_can_recover():
    selections = []
    selected = object()

    def get_endpoint():
        selections.append(selected)
        return selected

    route = LLMEndpointRoute(cast(Any, get_endpoint))
    bound = selected_model(route).copy()
    configured = with_request_options(route, extra_body={})
    assert selections == []
    for inspect in (bound.external_dependencies, configured.resolve):
        with pytest.raises(TypeError, match="get_endpoint must return"):
            inspect()
    selected = _endpoint("valid", [], [])
    assert configured.resolve().client is selected.client
    assert bound.external_dependencies()[0] is selected
    with pytest.raises(TypeError, match="callable"):
        LLMEndpointRoute(cast(Any, selected))


def test_chat_route_rejects_transcription_and_nested_routes():
    client = SimpleNamespace(audio=object(), models=object(), close=lambda: None)
    transcription = TranscriptionEndpoint(
        client=client, api_name="test", model_name="speech"
    )
    route = LLMEndpointRoute(lambda: MockLLMEndpoint([]))
    for invalid in (transcription, route):
        with pytest.raises(TypeError, match="get_endpoint must return"):
            LLMEndpointRoute(cast(Any, lambda: invalid)).resolve()
    with pytest.raises(TypeError, match="TranscriptionEndpoint"):
        resolve_transcription_endpoint(cast(Any, route))
    assert resolve_transcription_endpoint(transcription) is transcription
    assert route.external_dependencies() == ()


def test_switch_during_provider_call_keeps_inflight_endpoint():
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
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        models=object(),
        close=lambda: None,
    )
    first, second = [
        LLMEndpoint(client=client, api_name="test", model_name=name, stream=False)
        for name in ("first", "second")
    ]
    selected = first
    route = with_request_options(LLMEndpointRoute(lambda: selected), extra_body={})
    with ThreadPoolExecutor() as pool:
        inflight = pool.submit(
            call_llm_api, route, [Message(role="user", content="Hi")]
        )
        try:
            assert entered.wait(5)
            selected = second
            assert route.external_dependencies() == (second,)
        finally:
            release.set()
        assert inflight.result(timeout=5)[0] == "first"
    assert call_llm_api(route, [Message(role="user", content="Hi")])[0] == "second"
    assert requests == ["first", "second"]
