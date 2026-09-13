"""Agent inspection exposes the exact resources their tools execute against."""

import subprocess
import sys
from pathlib import Path

from roboz import Agent, Message, Str, factory, stop
from roboz.dependencies import ExecutableDependency
from roboz.llm import MockLLMEndpoint


@factory
def convert(input: Str, messages: list[Message], ctx: ExecutableDependency) -> Str:
    subprocess.run([ctx.require(), input.value], check=True)
    return input


def test_direct_executable_declaration_is_the_command_used(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    executable = ExecutableDependency(sys.executable)
    bound = convert(executable)
    agent = Agent(
        name="consumer",
        system_prompt="Complete the task.",
        agent_endpoint=MockLLMEndpoint([]),
        tools=[bound, stop],
    )
    assert agent.external_dependencies()[0] is executable
    assert bound.copy().external_dependencies()[0] is executable
    assert calls == []
    assert bound(Str(value="payload"), []).value == "payload"
    assert calls == [([executable.require(), "payload"], {"check": True})]
    assert isinstance(calls[0][0][0], Path)
