"""Unit coverage for lazy model endpoint selection."""

from types import SimpleNamespace
from roboz.llm import LLMEndpoint, LLMEndpointRoute, ModelSelector


def _model(name: str, resolutions: list[str]) -> LLMEndpoint:
    initialized = False

    def materialize():
        nonlocal initialized
        if not initialized:
            resolutions.append(name)
            initialized = True
        return client

    client = SimpleNamespace(
        chat=object(), models=object(), close=lambda: None, materialize=materialize
    )
    return LLMEndpoint(
        client=client, api_name="test", model_name=name, max_context_tokens=1_000
    )


def test_listing_and_selecting_do_not_materialize_models() -> None:
    resolutions: list[str] = []
    first = _model("first", resolutions)
    second = _model("second", resolutions)
    selector = ModelSelector(
        {"First": first, "Second": second},
        default=first,
    )

    assert [model.dependency_id for model in selector.models.values()] == [
        first.dependency_id,
        second.dependency_id,
    ]
    selector.select(second.dependency_id)

    assert selector.selected_model_id == second.dependency_id
    assert resolutions == []


def test_selected_endpoint_tracks_the_latest_selection() -> None:
    resolutions: list[str] = []
    first = _model("first", resolutions)
    second = _model("second", resolutions)
    selector = ModelSelector(
        {"First": first, "Second": second},
        default=first,
    )

    assert selector.selected_endpoint is first
    selector.select(second.dependency_id)

    assert selector.selected_endpoint is second
    assert resolutions == []


def test_unknown_model_does_not_change_selection() -> None:
    resolutions: list[str] = []
    endpoint = _model("first", resolutions)
    selector = ModelSelector(
        {"First": endpoint},
        default=endpoint,
    )

    try:
        selector.select("model:test:missing")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown selection should fail")

    assert selector.selected_model_id == endpoint.dependency_id
    assert resolutions == []


def test_live_route_follows_selection_without_resolving_during_discovery() -> None:
    resolutions: list[str] = []
    first = _model("first", resolutions)
    second = _model("second", resolutions)
    selector = ModelSelector({"First": first, "Second": second}, default=first)
    route = LLMEndpointRoute(lambda: selector.selected_endpoint)

    assert route.external_dependencies() == (first,)
    selector.select(second.dependency_id)
    assert route.external_dependencies() == (second,)
    assert resolutions == []
    assert route.resolve() is second
    assert route.materialize() is route
    assert route.resolve() is second.materialize()
    assert resolutions == ["second"]
