import pytest
from roboz.shed.agents import librarian as librarian_definition
from roboz.shed.agents import orchestrator as orchestrator_definition
from roboz.shed.sandbox import Sandbox

from roboz.models import Empty
from roboz.tools import stop
from roboz.agent import run_background_agent
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import default_event_sinks


def _specialist(name, subagents=()):
    definition = DeployableAgent(
        name=name,
        system_prompt="Be a specialist.",
        default_capabilities=(Capability(tools=(stop,)),),
        subagents=subagents,
    )
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    return definition


def _build(definition, sandbox, *, event_sinks=(), include_cli=False):
    return definition.build(
        event_sinks=event_sinks,
        event_sink_factory=lambda name: default_event_sinks(
            data_path=sandbox.project_logs_dir() / name,
            include_cli=include_cli,
        ),
    )


def test_librarian_defaults_snapshot_recursive_foreground_conversations(tmp_path):
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("project")
    child = _specialist("child", (_specialist("grandchild"),))
    background = _specialist("background", (_specialist("background_child"),))
    foreground = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
        subagents=(child,),
    )
    names = foreground.agent_names(include_background=False)
    librarian = librarian_definition(
        sandbox,
        names,
        agent_endpoint=MockLLMEndpoint([{"value": "Remember this conversation."}] * 3),
    )
    foreground.add_background_agents(librarian, background)

    root, (maintenance, _) = _build(foreground, sandbox)

    assert foreground.agent_names(include_background=False) == names
    assert not root.initial_messages
    assert root.default_tools[0].name == "start_background_agent_librarian"
    assert root.default_tools[0].description == run_background_agent.description
    assert not sandbox.projects_dir.exists()
    for name in names | {"background", "background_child"}:
        recorder = _specialist(name)
        recorder.set_agent_endpoint(
            MockLLMEndpoint(
                [
                    {
                        "action": "stop",
                        "rationale": "record",
                        "value": "x" * 21_000,
                    }
                ]
            )
        )
        _build(recorder, sandbox)[0].invoke()
    maintenance.default_tools[0](input=Empty(), messages=[])
    assert len(list(sandbox.project_snapshots_dir().rglob("*.md"))) == 3
    assert librarian.sandbox is sandbox
    assert librarian.watched_agent_names == names


def test_reconfiguration_does_not_redirect_built_agents(tmp_path):
    events, later_events = [], []
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("one")
    first_definition = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
    )
    first, _ = _build(first_definition, sandbox, event_sinks=(events.append,))
    one_project = sandbox.project_dir()
    one_logs = sandbox.project_logs_dir()

    sandbox.configure_scope("two")
    second_definition = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
    )
    second, _ = _build(second_definition, sandbox, event_sinks=(later_events.append,))
    two_project = sandbox.project_dir()
    two_logs = sandbox.project_logs_dir()

    assert first.pipe is not second.pipe
    assert first.pipe.data_path == one_logs / "orchestrator"
    assert second.pipe.data_path == two_logs / "orchestrator"
    assert not sandbox.projects_dir.exists()
    for project in (one_project, two_project):
        target = project / "note.txt"
        target.parent.mkdir(parents=True)
        target.write_text("before")
    first.copy(
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "apply_patch",
                    "rationale": "edit selected scope",
                    "path": "projects/one/note.txt",
                    "old_string": "before",
                    "new_string": "after",
                },
                {
                    "action": "apply_patch",
                    "rationale": "try other scope",
                    "path": "projects/two/note.txt",
                    "old_string": "before",
                    "new_string": "wrong",
                },
                {"action": "stop", "rationale": "done", "value": "finished"},
            ]
        )
    ).invoke()
    assert (one_project / "note.txt").read_text() == "after"
    assert (two_project / "note.txt").read_text() == "before"
    assert events and not later_events
    assert list(one_logs.rglob("*.json"))
    assert not two_logs.exists()


def test_orchestrator_requires_a_configured_scope(tmp_path):
    sandbox = Sandbox(tmp_path)
    with pytest.raises(ValueError, match="configure_scope"):
        orchestrator_definition(sandbox, agent_endpoint=MockLLMEndpoint([]))
    assert not list(tmp_path.iterdir())


def test_instance_preserves_maintenance_order_and_cli_output(tmp_path):
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("project")
    foreground = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint([]),
    )
    names = foreground.agent_names(include_background=False)
    foreground.add_background_agents(
        librarian_definition(
            sandbox,
            names,
            agent_endpoint=MockLLMEndpoint([]),
        )
    )

    root, (maintenance,) = _build(foreground, sandbox, include_cli=True)

    assert not root.initial_messages
    assert len(root.pipe.event_sinks) == len(maintenance.pipe.event_sinks) == 2
    assert [tool.name for tool in maintenance.default_tools] == [
        "snapshot_conversations",
        "consolidate_memory",
        "purge_logs",
        "purge_snapshots",
        "purge_memory",
        "stop_when_watched_agents_inactive",
        "sleep_between_runs",
    ]
    assert not list(tmp_path.iterdir())
