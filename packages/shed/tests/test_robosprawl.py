import pytest

from roboshed.deployments import robosprawl
from roboshed.deployments.robosprawl import RoboSprawl
from roboshed.sandbox import Sandbox
from roboz import Empty, ExternalDependencyKind, LazyExternalDependency, stop
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import Output, default_event_sinks


def _endpoint(name):
    def unexpected_materialization():
        pytest.fail("Configuration and build must not materialize a provider")

    return LazyExternalDependency(
        f"model:test:{name}",
        ExternalDependencyKind.MODEL_ENDPOINT,
        {},
        unexpected_materialization,
    )


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


def _recipe(sandbox):
    recipe = RoboSprawl()
    recipe.set_sandbox(sandbox)
    recipe.set_endpoint_getter(lambda: _endpoint("selected"))
    recipe.set_memory_endpoint(_endpoint("memory"))
    return recipe


def test_incomplete_configuration_reports_missing_inputs_without_work(tmp_path):
    assert robosprawl is RoboSprawl
    recipe = RoboSprawl()
    with pytest.raises(
        ValueError,
        match="RoboSprawl is missing: sandbox, endpoint_getter, memory_endpoint",
    ):
        recipe.build()
    recipe.set_sandbox(Sandbox(tmp_path).for_project("project"))
    with pytest.raises(ValueError, match="missing: endpoint_getter, memory_endpoint"):
        recipe.build()
    recipe.set_endpoint_getter(lambda: _endpoint("selected"))
    with pytest.raises(ValueError, match="missing: memory_endpoint"):
        recipe.build()
    recipe.set_memory_endpoint(_endpoint("memory"))

    root, (maintenance,) = recipe.build()

    assert root.name == "orchestrator"
    assert maintenance.name == "librarian"
    assert not list(tmp_path.iterdir())


def test_build_requires_a_valid_scoped_sandbox(tmp_path):
    recipe = _recipe(Sandbox(tmp_path))
    with pytest.raises(ValueError, match="configure_scope"):
        recipe.build()
    assert not list(tmp_path.iterdir())


def test_recipe_preserves_defaults_and_builds_independent_graphs(tmp_path):
    sandbox = Sandbox(tmp_path).for_project("project")
    recipe = _recipe(sandbox)
    specialist = _specialist(
        "specialist",
        subagents=(_specialist("nested"),),
        background_agents=(_specialist("hidden"),),
    )
    specialists = [specialist]
    capabilities = [Capability(tools=(stop.copy(name="extra_stop"),))]
    events = []
    sinks = [events.append]
    recipe.set_specialists(specialists)
    recipe.set_additional_capabilities(capabilities)
    recipe.set_interaction_mode(Output.API)
    recipe.set_event_sinks(sinks)
    specialists.clear()
    capabilities.clear()
    sinks.clear()

    first, first_backgrounds = recipe.build()
    second, second_backgrounds = recipe.build()

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
    assert first.interaction_mode == Output.API
    assert {"stop", "run_file_command", "apply_patch", "extra_stop", "specialist"} <= {
        tool.name for tool in first.tools
    }
    assert [tool.name for tool in first_backgrounds[-1].default_tools] == [
        "snapshot_conversations",
        "consolidate_memory",
        "purge_logs",
        "purge_snapshots",
        "purge_memory",
        "sleep_between_runs",
    ]
    assert not events
    assert not list(tmp_path.iterdir())


def test_librarian_watches_only_recursive_foreground_names(tmp_path):
    sandbox = Sandbox(tmp_path).for_project("project")
    recipe = _recipe(sandbox)
    recipe.set_specialists(
        (
            _specialist(
                "specialist",
                subagents=(_specialist("nested"),),
                background_agents=(_specialist("hidden"),),
            ),
        )
    )
    recipe.set_memory_endpoint(MockLLMEndpoint([{"value": "Remember this."}] * 3))
    _, (_, maintenance) = recipe.build()
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


def test_reconfiguration_preserves_built_paths_permissions_and_sinks(tmp_path):
    sandbox = Sandbox(tmp_path).for_project("one")
    recipe = _recipe(sandbox)
    first_events, second_events = [], []
    recipe.set_event_sinks((first_events.append,))
    sandbox.configure_scope("two")
    first, (first_memory,) = recipe.build()
    recipe.set_sandbox(sandbox)
    recipe.set_event_sinks((second_events.append,))
    second, (second_memory,) = recipe.build()
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
    recipe = _recipe(Sandbox(tmp_path).for_project("project"))
    recipe.set_endpoint_getter(lambda: selected)
    recipe.set_memory_endpoint(memory)
    root, (maintenance,) = recipe.build()
    assert selected in root.external_dependencies()
    assert memory in maintenance.external_dependencies()

    selected = replacement

    assert replacement in root.external_dependencies()
    assert memory in maintenance.external_dependencies()
    assert not list(tmp_path.iterdir())
