import pytest
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
)
from roboshed.deployments.robosprawl import robosprawl
from roboshed.sandbox import Sandbox

from roboz import Empty, stop
from roboz.agent import run_background_agent
from roboz.deployment import DeployableAgent, Capability, Deployment
from roboz.llm import MockLLMEndpoint


def _specialist(name, subagents=()):
    return DeployableAgent(
        name=name,
        agent_endpoint=MockLLMEndpoint([]),
        system_prompt="Be a specialist.",
        capabilities=(Capability(tools=(stop,)),),
        subagents=subagents,
    )


def test_profile_snapshots_recursive_foreground_conversations(tmp_path):
    sandbox = Sandbox(tmp_path)
    child = _specialist("child", (_specialist("grandchild"),))
    names = {"orchestrator", "child", "grandchild"}
    deployment = robosprawl(
        sandbox,
        "project",
        orchestrator_endpoint=MockLLMEndpoint([]),
        capabilities=(),
        subagents=(child,),
        memory_endpoint=MockLLMEndpoint([{"value": "Remember this conversation."}] * 3),
        librarian_capabilities=lambda sandbox, slug, watched: (
            ConversationSnapshots(sandbox, slug, watched, token_growth_threshold=1),
            MaintenanceCadence(sandbox, slug, watched),
        ),
    )
    root, (maintenance,) = deployment.build()
    assert deployment.root.agent_names(include_background=False) == names
    assert root.initial_messages == (sandbox.project_memory_dir("project"),)
    assert root.default_tools[0].name == "start_background_agent_librarian"
    assert root.default_tools[0].description == run_background_agent.description
    assert not sandbox.projects_dir.exists()
    for name in names:
        Deployment(
            root=_specialist(name),
            event_sink_factory=deployment.event_sink_factory,
        ).build()[0].copy(
            agent_endpoint=MockLLMEndpoint(
                [
                    {
                        "action": "stop",
                        "rationale": "record",
                        "value": "Keep the decision.",
                    },
                ]
            )
        ).invoke()
    maintenance.default_tools[0](input=Empty(), messages=[])
    assert len(list(sandbox.project_snapshots_dir("project").rglob("*.md"))) == 3


def test_profile_binds_fresh_project_capabilities_and_default_maintenance(tmp_path):
    seen = []

    def capability(permissions):
        seen.append(permissions)
        return Capability()

    memory = MockLLMEndpoint([])
    sandbox = Sandbox(tmp_path)
    deployments = [
        robosprawl(
            sandbox,
            slug,
            orchestrator_endpoint=MockLLMEndpoint([]),
            capabilities=(capability,),
            memory_endpoint=memory,
        )
        for slug in ("one", "two")
    ]
    assert seen == [sandbox.permissions(slug) for slug in ("one", "two")]
    assert (
        deployments[0].root.capabilities[-1] is not deployments[1].root.capabilities[-1]
    )
    for slug, deployment in zip(("one", "two"), deployments, strict=True):
        assert type(deployment) is Deployment
        assert str(sandbox.project_dir(slug)) in deployment.root.system_prompt
        assert "<file src=" not in deployment.root.system_prompt
        maintenance = deployment.root.background_agents[-1]
        assert maintenance.agent_endpoint is memory
        snapshots, consolidation, retention, cadence = maintenance.capabilities
        assert (
            snapshots.agent_names
            == consolidation.agent_names
            == cadence.agent_names
            == {"orchestrator"}
        )
        assert retention.sandbox is sandbox
        assert retention.project_slug == slug
        assert cadence.seconds == 120
        deployment.build()
        assert not sandbox.projects_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_profile_binds_custom_maintenance_and_excludes_background_branches(
    tmp_path, empty
):
    seen = []

    def maintenance(sandbox, slug, names):
        capabilities = (
            ()
            if empty
            else (
                MaintenanceCadence(sandbox, slug, names, seconds=17),
                ArtifactRetention(sandbox, slug, max_log_files=8),
            )
        )
        seen.append((sandbox, slug, names, capabilities))
        return capabilities

    child = _specialist("child", (_specialist("nested"),))
    background = _specialist("background", (_specialist("background_child"),))
    memory = MockLLMEndpoint([])
    sandbox = Sandbox(tmp_path)
    for slug in ("one", "two"):
        deployment = robosprawl(
            sandbox,
            slug,
            capabilities=(),
            orchestrator_endpoint=MockLLMEndpoint([]),
            memory_endpoint=memory,
            librarian_capabilities=maintenance,
            subagents=(child,),
            background_agents=(background,),
        )
        assert seen[-1][:3] == (sandbox, slug, {"orchestrator", "child", "nested"})
        definition = deployment.root.background_agents[-1]
        assert definition.capabilities == seen[-1][3]
        assert definition.agent_endpoint is memory
        if empty:
            with pytest.raises(ValueError, match="No default tools"):
                deployment.build()
        else:
            cadence, retention = definition.capabilities
            assert cadence.seconds == 17
            assert retention.max_log_files == 8
            assert cadence.sandbox is retention.sandbox is sandbox
            assert cadence.project_slug == retention.project_slug == slug
            deployment.build()
        assert not sandbox.projects_dir.exists()
    if not empty:
        assert seen[0][3][0] is not seen[1][3][0]


def test_profile_can_disable_memory_and_enable_cli_sinks(tmp_path):
    deployment = robosprawl(
        Sandbox(tmp_path),
        "project",
        orchestrator_endpoint=MockLLMEndpoint([]),
        memory_endpoint=MockLLMEndpoint([]),
        capabilities=(),
        seed_initial_messages_from_memory=False,
        include_cli_output=True,
    )
    root, (maintenance,) = deployment.build()
    assert not root.initial_messages
    assert len(root.pipe.event_sinks) == len(maintenance.pipe.event_sinks) == 2
    assert not list(tmp_path.iterdir())
