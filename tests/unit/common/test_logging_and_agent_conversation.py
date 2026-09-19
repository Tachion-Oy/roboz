import json
from pathlib import Path

import pytest

from roboz.agent.core import Agent
from roboz.tools import stop
from roboz.models import Empty, Invoke, Message, Role
from roboz.runtime.pipe import EventPipe
from roboz.runtime.persistence import RunStatus
from roboz.runtime.sinks import PersistenceSink
from roboz.tooling.decorators import tool
from roboz.llm.endpoints import MockLLMEndpoint


@pytest.fixture
def persisted_pipe(tmp_path: Path) -> tuple[EventPipe, PersistenceSink]:
    base = tmp_path / "stub_data"
    sink = PersistenceSink.for_path(base)
    return EventPipe(event_sinks=(sink,)), sink


# ---------- Persistence sink constructor ----------


def test_persistence_sink_accepts_directory_path(tmp_path: Path) -> None:
    base = tmp_path / "run_data"
    base.mkdir()
    sink = PersistenceSink.for_path(base)
    assert sink.conversations_location is None


def test_persistence_sink_rejects_data_path_that_is_not_a_directory(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "not_a_dir.json"
    file_path.write_text("{}")
    with pytest.raises(ValueError, match="data_path must be a folder"):
        PersistenceSink.for_path(file_path)


# ---------- EventPipe.emit_message (V2) ----------


def test_emit_message_creates_folder_and_file_if_missing(
    tmp_path: Path, persisted_pipe: tuple[EventPipe, PersistenceSink]
) -> None:
    pipe, sink = persisted_pipe
    pipe.initialize(dry_run=False, agent_name="test_agent")

    message = Message(role=Role.USER, content="hello world")
    pipe.emit_message(message)

    path = sink.conversations_location
    assert path is not None
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert data["agent_name"] == "test_agent"
    assert data["status"] == "running"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "hello world"
    assert data["messages"][0]["message_kind"] is None


def test_emit_message_appends_to_same_run(
    tmp_path: Path, persisted_pipe: tuple[EventPipe, PersistenceSink]
) -> None:
    pipe, sink = persisted_pipe
    pipe.initialize(dry_run=False, agent_name="test_agent")
    first_message = Message(role=Role.SYSTEM, content="first")
    second_message = Message(role=Role.ASSISTANT, content="second")

    pipe.emit_message(first_message)
    pipe.emit_message(second_message)

    path = sink.conversations_location
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "first"
    assert data["messages"][1]["content"] == "second"
    assert data["messages"][0]["sequence"] == 2
    assert data["messages"][1]["sequence"] == 3


# ---------- EventPipe.finalize_run ----------


def test_finalize_run_writes_status_and_ended_at(
    tmp_path: Path, persisted_pipe: tuple[EventPipe, PersistenceSink]
) -> None:
    pipe, sink = persisted_pipe
    pipe.initialize(dry_run=False, agent_name="fin_test")
    path = sink.conversations_location
    assert path is not None
    pipe.finalize_run(status=RunStatus.CANCELLED)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "cancelled"
    assert data["ended_at"] is not None
    assert data["started_at"] is not None
    assert data["messages"] == []


def test_finalize_run_noop_when_initialize_was_dry_run(tmp_path: Path) -> None:
    base = tmp_path / "dry"
    base.mkdir()
    sink = PersistenceSink.for_path(base)
    pipe = EventPipe(event_sinks=(sink,))
    pipe.initialize(dry_run=True, agent_name="x")
    pipe.finalize_run(status=RunStatus.COMPLETED)
    assert sink.conversations_location is None
    assert not any(base.rglob("*.json"))


# ---------- Tests for Agent integration with EventPipe ----------


@tool
def default_invoke(input: Empty, messages: list[Message]) -> Invoke:
    """Simple default tool that immediately asks Agent to call stop."""
    return Invoke(
        action="stop",
        rationale="testing conversation logging",
        value="logging",  # type: ignore
    )


def test_agent_creates_conversation_folder_and_saves_messages(
    tmp_path: Path,
) -> None:
    rbz_root = tmp_path / "roboz_data"
    scripted = [
        dict(action="stop", rationale="testing conversation logging", value="logging")
    ]
    sink = PersistenceSink.for_path(rbz_root)

    agent = Agent(
        name="agent_data",
        description="Agent data description.",
        event_sinks=(sink,),
        tools=[stop],
        system_prompt="system prompt",
        agent_endpoint=MockLLMEndpoint(scripted),
        initial_messages=None,
    )
    agent.invoke()

    conversation_file = sink.conversations_location
    assert conversation_file is not None

    assert conversation_file.is_file()
    rel = conversation_file.relative_to(rbz_root)
    assert len(rel.parts) == 4

    data = json.loads(conversation_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert "messages" in data
    assert len(data["messages"]) >= 1
    assert data["status"] == "completed"
    assert data["ended_at"] is not None
    assert data["agent_name"] == "agent_data"
    assert data["metadata"]["agent_description"] == "Agent data description."

    first_path = conversation_file

    sink = PersistenceSink.for_path(rbz_root)
    agent = Agent(
        name="agent_other",
        description="Agent other description.",
        event_sinks=(sink,),
        tools=[stop],
        system_prompt="system prompt",
        agent_endpoint=MockLLMEndpoint(scripted),
        initial_messages=None,
    )
    agent.invoke()

    conversation_file = sink.conversations_location
    assert conversation_file is not None
    assert conversation_file.is_file()
    assert len(conversation_file.relative_to(rbz_root).parts) == 4
    assert conversation_file != first_path
    data = json.loads(conversation_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert "messages" in data
    assert data["agent_name"] == "agent_other"
    assert data["metadata"]["agent_description"] == "Agent other description."
