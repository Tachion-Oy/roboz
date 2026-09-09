import pytest
from roboshed.agents import librarian as librarian_definition
from roboshed.agents import orchestrator
from roboshed.capabilities import ConversationSnapshots, MaintenanceCadence
from roboshed.deployments.robosprawl import AgenticFactory, RoboSprawl
from roboshed.sandbox import Sandbox

from roboz import Empty, stop
from roboz.deployment import AgentDefinition, Capability, SubAgentSpec
from roboz.llm import MockLLMEndpoint


def _specialist(name, subagents=()):
    return AgentDefinition(
        name=name,
        agent_endpoint=MockLLMEndpoint([]),
        system_prompt="Be a specialist.",
        capabilities=(Capability(tools=(stop,)),),
        subagents=subagents,
    )


def test_factory_tracks_nested_agents_and_isolates_persistence_sinks(tmp_path):
    sandbox = Sandbox(tmp_path)
    project_slug = "project"
    grandchild = _specialist("grandchild")
    child = _specialist(
        "child", (SubAgentSpec(grandchild, "ask_grandchild", "Delegate."),)
    )
    definition = orchestrator(
        agent_endpoint=MockLLMEndpoint([]),
        subagents=(SubAgentSpec(child, "ask_child", "Delegate."),),
    )
    factory = AgenticFactory(
        sandbox=sandbox,
        project_slug=project_slug,
        orchestrator=definition,
        librarian=librarian_definition(
            agent_endpoint=MockLLMEndpoint(
                [{"value": "Remember this conversation."}] * 3
            ),
            capabilities=(
                ConversationSnapshots(
                    sandbox,
                    project_slug,
                    definition.agent_names(),
                    token_growth_threshold=1,
                ),
                MaintenanceCadence(
                    sandbox, project_slug, definition.agent_names()
                ),
            ),
        ),
    )
    events = []
    bundle = factory.build(event_sinks=(events.append,))
    (librarian,) = bundle.background_agents
    assert factory.agent_names() == {"orchestrator", "child", "grandchild"}
    logs = sandbox.project_logs_dir(project_slug)
    assert bundle.agent.pipe.data_path == logs / "orchestrator"
    assert librarian.pipe.data_path == logs / "librarian"
    assert events.append in bundle.agent.pipe.event_sinks
    assert events.append not in librarian.pipe.event_sinks
    assert not tmp_path.joinpath("projects").exists()
    assert not librarian.pipe.cancelled
    assert [t.name for t in bundle.agent.default_tools] == [
        "start_background_agent_librarian"
    ]
    for name in factory.agent_names():
        AgenticFactory(
            sandbox=sandbox,
            project_slug=project_slug,
            orchestrator=_specialist(name),
        ).build()[0].copy(
            agent_endpoint=MockLLMEndpoint(
                [
                    {
                        "action": "stop",
                        "rationale": "record",
                        "value": "Keep the project decision.",
                    }
                ]
            )
        ).invoke()
    librarian.default_tools[0](input=Empty(), messages=[])
    assert len(list(sandbox.project_snapshots_dir(project_slug).rglob("*.md"))) == 3


@pytest.mark.parametrize("name", ["orchestrator", "child"])
def test_factory_rejects_colliding_recursive_names_before_build(tmp_path, name):
    sandbox = Sandbox(tmp_path)
    child = _specialist(
        "child", (SubAgentSpec(_specialist(name), "nested", "Delegate."),)
    )
    factory = AgenticFactory(
        sandbox=sandbox,
        project_slug="test",
        orchestrator=orchestrator(
            agent_endpoint=MockLLMEndpoint([]),
            subagents=(SubAgentSpec(child, "delegate", "Delegate."),),
        ),
    )
    with pytest.raises(ValueError, match="unique"):
        factory.build()
    assert list(tmp_path.iterdir()) == []


def test_librarian_name_cannot_collide_with_root(tmp_path):
    sandbox = Sandbox(tmp_path)
    factory = AgenticFactory(
        sandbox=sandbox,
        project_slug="test",
        orchestrator=_specialist("librarian"),
        librarian=librarian_definition(
            capabilities=(MaintenanceCadence(sandbox, "test", {"librarian"}),),
        ),
    )
    with pytest.raises(ValueError, match="librarian name"):
        factory.build()


def test_background_specialist_names_cannot_overlap_the_foreground(tmp_path):
    sandbox = Sandbox(tmp_path)
    root = _specialist("foreground")
    background = _specialist(
        "maintenance",
        (SubAgentSpec(_specialist("foreground"), "delegate", "Delegate."),),
    )
    factory = AgenticFactory(
        sandbox=sandbox,
        project_slug="test",
        orchestrator=root,
        librarian=background,
    )
    with pytest.raises(ValueError, match="librarian names"):
        factory.build()
    assert list(tmp_path.iterdir()) == []


def test_inspection_preserves_project_folders_and_cleans_failed_build(tmp_path):
    from dataclasses import replace
    from pathlib import Path
    from roboshed.deployments.robosprawl import inspect_dependencies

    sandbox = Sandbox(
        tmp_path / "absent",
        shared="team",
        logs_dir=Path("logs-custom"),
    )
    inspected = []

    def failing(sandbox, project_slug, *, endpoint_getter, event_sinks):
        inspected.append((sandbox, project_slug))
        sandbox.project_dir(project_slug).mkdir(parents=True)
        raise RuntimeError("recipe failed")

    with pytest.raises(RuntimeError, match="recipe failed"):
        inspect_dependencies(
            failing,
            sandbox=sandbox,
            project_slug="project",
            endpoint_getter=lambda: None,
            registrations=(),
        )
    inspected_sandbox, inspected_slug = inspected[0]
    assert replace(inspected_sandbox, root=sandbox.root) == sandbox
    assert inspected_slug == "project"
    assert not inspected_sandbox.resolved_root.exists()
    assert not sandbox.resolved_root.exists()


def test_recipe_binds_fresh_project_capabilities_and_default_maintenance(tmp_path):
    seen = []

    def capability(permissions):
        seen.append(permissions)
        return Capability()

    memory = MockLLMEndpoint([])
    deployment = RoboSprawl(capabilities=(capability,), memory_endpoint=memory)
    sandbox = Sandbox(tmp_path)
    project_slugs = ("one", "two")
    factories = [
        deployment(sandbox, slug, orchestrator_endpoint=MockLLMEndpoint([]))
        for slug in project_slugs
    ]
    assert seen == [sandbox.permissions(slug) for slug in project_slugs]
    assert (
        factories[0].orchestrator.capabilities[-1]
        is not factories[1].orchestrator.capabilities[-1]
    )
    for slug, factory in zip(project_slugs, factories, strict=True):
        assert str(sandbox.project_dir(slug)) in factory.orchestrator.system_prompt
        assert "<file src=" not in factory.orchestrator.system_prompt
        assert factory.librarian is not None
        assert factory.librarian.agent_endpoint is memory
        snapshots, consolidation, retention, cadence = factory.librarian.capabilities
        assert (
            snapshots.agent_names
            == consolidation.agent_names
            == cadence.agent_names
            == {"orchestrator"}
        )
        assert retention.sandbox is sandbox
        assert retention.project_slug == slug
        assert cadence.seconds == 120
        factory.build()
        assert not sandbox.projects_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_recipe_binds_custom_librarian_capabilities_per_project(tmp_path, empty):
    from roboshed.capabilities import ArtifactRetention

    seen = []

    def maintenance(sandbox, project_slug, names):
        capabilities = () if empty else (
            MaintenanceCadence(sandbox, project_slug, names, seconds=17),
            ArtifactRetention(sandbox, project_slug, max_log_files=8),
        )
        seen.append((sandbox, project_slug, names, capabilities))
        return capabilities

    child = _specialist(
        "child", (SubAgentSpec(_specialist("nested"), "ask_nested", "Delegate."),)
    )
    memory = MockLLMEndpoint([])
    deployment = RoboSprawl(
        capabilities=(),
        memory_endpoint=memory,
        librarian_capabilities=maintenance,
        subagents=(SubAgentSpec(child, "ask_child", "Delegate."),),
    )
    sandbox = Sandbox(tmp_path)
    for name in ("one", "two"):
        factory = deployment(
            sandbox, name, orchestrator_endpoint=MockLLMEndpoint([])
        )
        assert seen[-1][:3] == (
            sandbox,
            name,
            {"orchestrator", "child", "nested"},
        )
        assert factory.librarian is not None
        assert factory.librarian.capabilities == seen[-1][3]
        assert factory.librarian.agent_endpoint is memory
        if not empty:
            cadence, retention = factory.librarian.capabilities
            assert cadence.seconds == 17
            assert retention.max_log_files == 8
            assert cadence.sandbox is retention.sandbox is sandbox
            assert cadence.project_slug == retention.project_slug == name
            factory.build()
        else:
            with pytest.raises(ValueError, match="No default tools"):
                factory.build()
        assert not sandbox.projects_dir.exists()
    assert len(seen) == 2
    if not empty:
        assert seen[0][3][0] is not seen[1][3][0]


def test_inspection_infers_registration_from_first_duplicate(tmp_path):
    from roboshed.dependency_health import check_executable
    from roboshed.deployments.robosprawl import RoboSprawlBundle, inspect_dependencies
    from roboz import ExecutableDependency, ExternalDependencyKind, LazyExternalDependency

    first = ExecutableDependency("python")
    later = LazyExternalDependency(
        first.dependency_id,
        ExternalDependencyKind.NETWORK_SERVICE,
        {},
        lambda: (_ for _ in ()).throw(AssertionError("must not materialize")),
    )

    def factory(sandbox, project_slug, *, endpoint_getter, event_sinks):
        return RoboSprawlBundle(_specialist("root").build())

    bound, = inspect_dependencies(
        factory,
        sandbox=Sandbox(tmp_path),
        project_slug="project",
        endpoint_getter=lambda: None,
        registrations=None,
        additional_dependencies=(first, later),
    )
    assert bound.dependency is first
    assert bound.check is check_executable
