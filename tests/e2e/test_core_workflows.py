"""Credential-free E2E contracts, also executed from installed wheels."""

import tempfile
from pathlib import Path

from roboz import Agent, Ctx, stop
from roboz.agent.subagent import run_subagent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe, PersistenceSink, RunLifecycleEvent
from roboz.tools.librarian import LibrarianConstructor, LibrarianPaths, LibrarianTuning


def test_subagent_completion(tmp_path: Path) -> None:
    events = []
    child = Agent(
        name="child",
        interaction_mode=None,
        tools=[stop],
        system_prompt="Finish.",
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "stop", "rationale": "finished", "value": "child completed"},
            ]
        ),
    )
    delegate = run_subagent(Ctx(agent=child))
    parent = Agent(
        name="parent",
        interaction_mode=None,
        tools=[delegate, stop],
        system_prompt="Delegate, then finish.",
        event_pipe=EventPipe(
            event_sinks=[events.append, PersistenceSink.for_path(tmp_path)]
        ),
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": delegate.name, "rationale": "delegate"},
                {
                    "action": "stop",
                    "rationale": "finished",
                    "value": "parent completed",
                },
            ]
        ),
    )
    result, messages = parent.invoke()
    assert result.value == "parent completed"
    assert any("child completed" in message.content for message in messages)
    assert any(
        isinstance(event, RunLifecycleEvent) and event.kind == "stopped"
        for event in events
    )
    assert list(tmp_path.rglob("*.json"))


def test_conversation_snapshot_memory_retention(tmp_path: Path) -> None:
    paths = LibrarianPaths(
        tmp_path / "logs", tmp_path / "snapshots", tmp_path / "memory"
    )
    author = Agent(
        name="author",
        interaction_mode=None,
        tools=[stop],
        system_prompt="Remember the project decision.",
        initial_messages=["The project uses a blue robot emblem."],
        event_pipe=EventPipe(
            event_sinks=[PersistenceSink.for_path(paths.conversation_root / "author")]
        ),
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "stop",
                    "rationale": "record decision",
                    "value": "Use the blue robot.",
                },
            ]
        ),
    )
    author.invoke()
    source = next((paths.conversation_root / "author").rglob("*.json"))
    librarian = LibrarianConstructor(
        snapshot_endpoint=MockLLMEndpoint(
            [
                {"value": "The project uses a blue robot emblem."},
                {"value": "Retain the blue robot emblem decision."},
            ]
        ),
        tuning=LibrarianTuning(
            min_pending_snapshots=1,
            token_growth_threshold=1,
            sleep_seconds=0,
            max_snapshot_files=0,
            max_log_files=0,
            max_memory_files=1,
        ),
    ).build(paths=paths, agent_names={"author"})
    result, _ = librarian.invoke()
    assert "project idle" in result.value
    memory = list(paths.memory_root.glob("*.md"))
    assert len(memory) == 1
    assert "blue robot emblem" in memory[0].read_text()
    # Retention happens after consolidation: the memory survives its sources.
    assert not source.exists()
    assert not list(paths.snapshot_root.rglob("*.md"))
    assert not any(path.is_dir() for path in paths.snapshot_root.iterdir())
    assert list((paths.conversation_root / "librarian").rglob("*.json"))


if __name__ == "__main__":
    for scenario in (
        test_subagent_completion,
        test_conversation_snapshot_memory_retention,
    ):
        with tempfile.TemporaryDirectory() as directory:
            scenario(Path(directory))
        print(f"PASS {scenario.__name__}")
