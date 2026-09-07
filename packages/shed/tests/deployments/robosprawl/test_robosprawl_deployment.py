import pytest
from roboshed.agents import librarian as librarian_definition
from roboshed.agents import orchestrator
from roboshed.capabilities import ConversationSnapshots, MaintenanceCadence
from roboshed.deployments.robosprawl import AgenticFactory
from roboshed.workspace import Project, Workspace

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
    project = Project(Workspace(tmp_path), "project")
    grandchild = _specialist("grandchild")
    child = _specialist(
        "child", (SubAgentSpec(grandchild, "ask_grandchild", "Delegate."),)
    )
    definition = orchestrator(
        agent_endpoint=MockLLMEndpoint([]),
        subagents=(SubAgentSpec(child, "ask_child", "Delegate."),),
    )
    factory = AgenticFactory(
        project=project,
        orchestrator=definition,
        librarian=librarian_definition(
            agent_endpoint=MockLLMEndpoint(
                [{"value": "Remember this conversation."}] * 3
            ),
            capabilities=(
                ConversationSnapshots(
                    project, definition.agent_names(), token_growth_threshold=1
                ),
                MaintenanceCadence(project, definition.agent_names()),
            ),
        ),
    )
    events = []
    bundle = factory.build(event_sinks=(events.append,))
    (librarian,) = bundle.background_agents
    assert factory.agent_names() == {"orchestrator", "child", "grandchild"}
    assert bundle.agent.pipe.data_path == project.logs / "orchestrator"
    assert librarian.pipe.data_path == project.logs / "librarian"
    assert events.append in bundle.agent.pipe.event_sinks
    assert events.append not in librarian.pipe.event_sinks
    assert not tmp_path.joinpath("projects").exists()
    assert not librarian.pipe.cancelled
    assert [t.name for t in bundle.agent.default_tools] == [
        "start_background_agent_librarian"
    ]
    for name in factory.agent_names():
        AgenticFactory(project=project, orchestrator=_specialist(name)).build()[0].copy(
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
    assert len(list(project.snapshots.rglob("*.md"))) == 3


@pytest.mark.parametrize("name", ["orchestrator", "child"])
def test_factory_rejects_colliding_recursive_names_before_build(tmp_path, name):
    project = Project(Workspace(tmp_path), "test")
    child = _specialist(
        "child", (SubAgentSpec(_specialist(name), "nested", "Delegate."),)
    )
    factory = AgenticFactory(
        project=project,
        orchestrator=orchestrator(
            agent_endpoint=MockLLMEndpoint([]),
            subagents=(SubAgentSpec(child, "delegate", "Delegate."),),
        ),
    )
    with pytest.raises(ValueError, match="unique"):
        factory.build()
    assert list(tmp_path.iterdir()) == []


def test_librarian_name_cannot_collide_with_root(tmp_path):
    project = Project(Workspace(tmp_path), "test")
    factory = AgenticFactory(
        project=project,
        orchestrator=_specialist("librarian"),
        librarian=librarian_definition(
            capabilities=(MaintenanceCadence(project, {"librarian"}),),
        ),
    )
    with pytest.raises(ValueError, match="librarian name"):
        factory.build()


def test_background_specialist_names_cannot_overlap_the_foreground(tmp_path):
    project = Project(Workspace(tmp_path), "test")
    root = _specialist("foreground")
    background = _specialist(
        "maintenance",
        (SubAgentSpec(_specialist("foreground"), "delegate", "Delegate."),),
    )
    factory = AgenticFactory(project=project, orchestrator=root, librarian=background)
    with pytest.raises(ValueError, match="librarian names"):
        factory.build()
    assert list(tmp_path.iterdir()) == []
