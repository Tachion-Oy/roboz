from dataclasses import replace

import pytest
from roboshed.agents import librarian as librarian_definition
from roboshed.agents import orchestrator as orchestrator_definition
from roboshed.deployments import Deployment
from roboshed.sandbox import Sandbox

from roboz import Empty, stop
from roboz.agent import run_background_agent
from roboz.deployment import DeployableAgent, Capability
from roboz.llm import MockLLMEndpoint


def _specialist(name, subagents=()):
    return DeployableAgent(
        name=name,
        agent_endpoint=MockLLMEndpoint([]),
        system_prompt="Be a specialist.",
        capabilities=(Capability(tools=(stop,)),),
        subagents=subagents,
    )


def test_librarian_defaults_snapshot_recursive_foreground_conversations(tmp_path):
    sandbox = Sandbox(tmp_path)
    child = _specialist("child", (_specialist("grandchild"),))
    background = _specialist("background", (_specialist("background_child"),))
    foreground = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
        subagents=(child,),
    )
    names = foreground.agent_names(include_background=False)
    deployment = Deployment(
        sandbox=sandbox,
        agent=replace(
            foreground,
            background_agents=(
                librarian_definition(
                    sandbox,
                    names,
                    agent_endpoint=MockLLMEndpoint(
                        [{"value": "Remember this conversation."}] * 3
                    ),
                ),
                background,
            ),
        ),
    )
    deployment.sandbox.configure_scope("project")
    root, (maintenance, _) = deployment.build()
    sandbox = deployment.sandbox
    assert deployment.agent.agent_names(include_background=False) == names
    assert not root.initial_messages
    assert root.default_tools[0].name == "start_background_agent_librarian"
    assert root.default_tools[0].description == run_background_agent.description
    assert not sandbox.projects_dir.exists()
    for name in names | {"background", "background_child"}:
        recorder = replace(
            _specialist(name),
            agent_endpoint=MockLLMEndpoint([
                {
                    "action": "stop",
                    "rationale": "record",
                    "value": "x" * 21_000,
                }
            ]),
        )
        Deployment(agent=recorder, sandbox=sandbox).build()[0].invoke()
    maintenance.default_tools[0](input=Empty(), messages=[])
    assert len(list(sandbox.project_snapshots_dir().rglob("*.md"))) == 3
    # Building does not mutate the configured Librarian definition.
    snapshots = deployment.agent.background_agents[0].capabilities[0]
    assert snapshots.sandbox is sandbox and snapshots.agent_names == names


def test_reconfiguration_does_not_redirect_built_agents(tmp_path):
    events, later_events = [], []
    sandbox = Sandbox(tmp_path)
    deployment = Deployment(
        sandbox=sandbox,
        agent=orchestrator_definition(
            sandbox,
            agent_endpoint=MockLLMEndpoint([]),
        ),
    )
    deployment.event_sinks.append(events.append)
    sandbox.configure_scope("one")
    first, _ = deployment.build()
    sandbox.configure_scope("two")
    deployment.event_sinks[:] = [later_events.append]
    second, _ = deployment.build()
    assert first.pipe is not second.pipe
    assert first.pipe.data_path == sandbox.project_logs_dir("one") / "orchestrator"
    assert second.pipe.data_path == sandbox.project_logs_dir("two") / "orchestrator"
    assert not sandbox.projects_dir.exists()
    for folder in ("one", "two"):
        target = sandbox.project_dir(folder) / "note.txt"
        target.parent.mkdir(parents=True)
        target.write_text("before")
    first.copy(agent_endpoint=MockLLMEndpoint([
        {
            "action": "apply_patch", "rationale": "edit selected scope",
            "path": "projects/one/note.txt", "old_string": "before", "new_string": "after",
        },
        {
            "action": "apply_patch", "rationale": "try other scope",
            "path": "projects/two/note.txt", "old_string": "before", "new_string": "wrong",
        },
        {"action": "stop", "rationale": "done", "value": "finished"},
    ])).invoke()
    assert (sandbox.project_dir("one") / "note.txt").read_text() == "after"
    assert (sandbox.project_dir("two") / "note.txt").read_text() == "before"
    assert events and not later_events
    assert list(sandbox.project_logs_dir("one").rglob("*.json"))
    assert not sandbox.project_logs_dir("two").exists()


def test_instance_requires_scope_before_building_capabilities(tmp_path):
    sandbox = Sandbox(tmp_path)
    class Unexpected:
        def build(self, pipe, *, default_endpoint):
            pytest.fail("must validate scope first")

    deployment = Deployment(
        sandbox=sandbox,
        agent=orchestrator_definition(sandbox, agent_endpoint=MockLLMEndpoint([])),
        additional_capabilities=(Unexpected(),),
    )
    with pytest.raises(ValueError, match="configure_scope"):
        deployment.build()
    other = replace(deployment, event_sinks=[])
    deployment.event_sinks.append(lambda event: None)
    assert not other.event_sinks
    assert not list(tmp_path.iterdir())


def test_instance_preserves_maintenance_order_and_cli_output(tmp_path):
    sandbox = Sandbox(tmp_path)
    foreground = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
    )
    names = foreground.agent_names(include_background=False)
    deployment = Deployment(
        sandbox=sandbox,
        agent=replace(
            foreground,
            background_agents=(
                librarian_definition(
                    sandbox,
                    names,
                    agent_endpoint=MockLLMEndpoint([]),
                ),
            ),
        ),
        include_cli_output=True,
    )
    deployment.sandbox.configure_scope("project")
    root, (maintenance,) = deployment.build()
    assert not root.initial_messages
    assert len(root.pipe.event_sinks) == len(maintenance.pipe.event_sinks) == 2
    assert [tool.name for tool in maintenance.default_tools] == [
        "snapshot_conversations", "consolidate_memory",
        "purge_logs", "purge_snapshots", "purge_memory", "sleep_between_runs",
    ]
    assert not list(tmp_path.iterdir())
