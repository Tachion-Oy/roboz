from types import SimpleNamespace
import importlib

from roboshed.sandbox import Sandbox
from roboz import stop
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import LLMEndpoint, MockLLMEndpoint
from roboz.runtime import Output

recipe_module = importlib.import_module("roboshed.deployments.robosprawl")


def _specialist(name, *, subagents=(), background_agents=()):
    definition = DeployableAgent(
        name=name,
        system_prompt="Complete specialist work.",
        default_capabilities=(Capability(tools=(stop,)),),
        subagents=subagents,
        background_agents=background_agents,
    )
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    return definition


def test_recipe_watches_complete_foreground_and_builds_independent_graphs(
    tmp_path, monkeypatch
):
    sandbox = Sandbox(tmp_path).for_project("project")

    def forbidden():
        raise AssertionError("Build and discovery initialized the selected client")

    selected = LLMEndpoint(
        client=SimpleNamespace(
            chat=object(), models=object(), close=forbidden, materialize=forbidden
        ),
        api_name="test",
        model_name="selected",
    )
    hidden = _specialist("hidden")
    specialist = _specialist(
        "specialist",
        subagents=(_specialist("nested"),),
        background_agents=(hidden,),
    )
    watched = []
    original_librarian = recipe_module.librarian

    def capture_librarian(sandbox, names, *, agent_endpoint):
        watched.append(frozenset(names))
        return original_librarian(
            sandbox,
            names,
            agent_endpoint=agent_endpoint,
        )

    monkeypatch.setattr(recipe_module, "librarian", capture_librarian)
    events = []

    def build():
        return recipe_module.robosprawl(
            sandbox,
            endpoint_getter=lambda: selected,
            memory_endpoint=MockLLMEndpoint([]),
            additional_capabilities=(Capability(),),
            specialists=(specialist,),
            interaction_mode=Output.API,
            event_sinks=(events.append,),
        )

    first, first_backgrounds = build()
    second, second_backgrounds = build()

    assert watched == [
        frozenset({"orchestrator", "specialist", "nested"}),
        frozenset({"orchestrator", "specialist", "nested"}),
    ]
    assert [agent.name for agent in first_backgrounds] == ["hidden", "librarian"]
    assert [agent.name for agent in second_backgrounds] == ["hidden", "librarian"]
    assert first is not second
    assert first.pipe is not second.pipe
    for previous, fresh in zip(first_backgrounds, second_backgrounds, strict=True):
        assert previous is not fresh
        assert previous.pipe is not fresh.pipe
    assert first.initial_messages[0] == sandbox.project_memory_dir()
    assert "Project: project" in first.initial_messages[1]
    assert events.append in first.pipe.event_sinks
    assert all(
        events.append not in agent.pipe.event_sinks for agent in first_backgrounds
    )
    assert selected in first.external_dependencies()
    assert "materialized" not in selected.__dict__
