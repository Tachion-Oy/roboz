from types import SimpleNamespace
import importlib

from roboshed.sandbox import Sandbox
import pytest

from roboz.models import Empty
from roboz.tools import stop
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import LLMEndpoint, MockLLMEndpoint
from roboz.runtime import default_event_sinks

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


def _endpoint(name):
    def forbidden():
        pytest.fail("Building and inspecting must not initialize a client")

    return LLMEndpoint(
        client=SimpleNamespace(
            chat=object(), models=object(), close=forbidden, materialize=forbidden
        ),
        api_name="test",
        model_name=name,
    )


def _build_recipe(sandbox, **choices):
    return recipe_module.robosprawl(
        sandbox,
        endpoint_getter=choices.pop("endpoint_getter", lambda: _endpoint("selected")),
        memory_endpoint=choices.pop("memory_endpoint", _endpoint("memory")),
        additional_capabilities=(),
        specialists=choices.pop("specialists", ()),
        event_sinks=choices.pop("event_sinks", ()),
    )


def test_build_requires_a_valid_scoped_sandbox(tmp_path):
    with pytest.raises(ValueError, match="configure_scope"):
        _build_recipe(Sandbox(tmp_path))
    assert not list(tmp_path.iterdir())


def test_librarian_watches_only_recursive_foreground_names(tmp_path):
    sandbox = Sandbox(tmp_path).for_project("project")
    specialists = (
        _specialist(
            "specialist",
            subagents=(_specialist("nested"),),
            background_agents=(_specialist("hidden"),),
        ),
    )
    _, (_, maintenance) = _build_recipe(
        sandbox,
        specialists=specialists,
        memory_endpoint=MockLLMEndpoint([{"value": "Remember this."}] * 3),
    )
    for name in ("orchestrator", "specialist", "nested", "hidden", "unrelated"):
        recorder = _specialist(name)
        recorder.set_agent_endpoint(
            MockLLMEndpoint(
                [
                    {"action": "stop", "rationale": "record", "value": "x" * 21_000},
                ]
            )
        )
        recorder.build(
            event_sinks=default_event_sinks(
                data_path=sandbox.project_logs_dir() / name,
                include_cli=False,
            )
        )[0].invoke()

    maintenance.default_tools[0](input=Empty(), messages=[])

    snapshots = list(sandbox.project_snapshots_dir().rglob("*.md"))
    assert len(snapshots) == 3
    watched_ids = {
        path.stem
        for name in ("orchestrator", "specialist", "nested")
        for path in (sandbox.project_logs_dir() / name).rglob("*.json")
    }
    assert {path.parent.name for path in snapshots} == watched_ids


def test_later_build_preserves_built_paths_permissions_and_sinks(tmp_path):
    sandbox = Sandbox(tmp_path).for_project("one")
    first_events, second_events = [], []
    first, (first_memory,) = _build_recipe(sandbox, event_sinks=(first_events.append,))
    sandbox.configure_scope("two")
    second, (second_memory,) = _build_recipe(
        sandbox, event_sinks=(second_events.append,)
    )
    one, two = sandbox.for_project("one"), sandbox.for_project("two")
    assert first.pipe.data_path == one.project_logs_dir() / "orchestrator"
    assert first_memory.pipe.data_path == one.project_logs_dir() / "librarian"
    assert second.pipe.data_path == two.project_logs_dir() / "orchestrator"
    assert second_memory.pipe.data_path == two.project_logs_dir() / "librarian"
    assert "Project: one" in first.initial_messages[1]
    assert "Project: two" in second.initial_messages[1]
    for project in (one, two):
        target = project.project_dir() / "note.txt"
        target.parent.mkdir(parents=True)
        target.write_text("before")
    first.copy(
        default_tools=(),
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "apply_patch",
                    "rationale": "edit own project",
                    "path": "projects/one/note.txt",
                    "old_string": "before",
                    "new_string": "after",
                },
                {
                    "action": "apply_patch",
                    "rationale": "try another project",
                    "path": "projects/two/note.txt",
                    "old_string": "before",
                    "new_string": "wrong",
                },
                {"action": "stop", "rationale": "done", "value": "finished"},
            ]
        ),
    ).invoke()
    assert (one.project_dir() / "note.txt").read_text() == "after"
    assert (two.project_dir() / "note.txt").read_text() == "before"
    assert first_events and not second_events
    assert list(one.project_logs_dir().rglob("*.json"))
    assert not two.project_logs_dir().exists()


def test_built_root_keeps_live_selection_and_independent_memory_endpoint(tmp_path):
    selected, replacement, memory = (
        _endpoint(name) for name in ("one", "two", "memory")
    )
    root, (maintenance,) = _build_recipe(
        Sandbox(tmp_path).for_project("project"),
        endpoint_getter=lambda: selected,
        memory_endpoint=memory,
    )
    assert selected in root.external_dependencies()
    assert memory in maintenance.external_dependencies()

    selected = replacement

    assert replacement in root.external_dependencies()
    assert memory in maintenance.external_dependencies()
    assert not list(tmp_path.iterdir())
