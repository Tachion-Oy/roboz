"""Arbitrary Bash commands run only after the user approves each one."""

import json
import os
import shutil
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.deployment import Capability, DeployableAgent, ToolLabel
from roboz.exceptions import UserInputUnavailableError
from roboz.llm import MockLLMEndpoint
from roboz.models import Role, Str, render_content
from roboz.runtime import EventPipe
from roboz.shed.capabilities import ShellCommands
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools import get_run_shell_command
from roboz.tools import stop

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="shell commands require POSIX and Bash",
)


def _run(base: Path, command: str, monkeypatch: pytest.MonkeyPatch, reply) -> tuple:
    """Return the prompts shown and each chained tool's output by caller."""
    prompts: list[str] = []

    def answer(message: str, with_reply: bool, timeout: float | None = None):
        prompts.append(message)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(runtime, "interact_with_user", answer)
    agent = Agent(
        name="shell_command_test",
        system_prompt="Run the requested command.",
        tools=[*get_run_shell_command(base=base, pipe=EventPipe()), stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {"action": "run_shell_command", "rationale": "Run", "command": command},
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    outputs = [json.loads(render_content(m)) for m in messages if m.role == Role.USER]
    return prompts, {output["caller"]: output for output in outputs}


def test_approved_command_runs_with_shell_operators_in_the_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "echo one && echo two | tr a-z A-Z; false || pwd > where.txt"
    prompts, outputs = _run(tmp_path, command, monkeypatch, Str(value=" Yes\n"))

    assert prompts == [
        f"The agent wants to run this command in `{tmp_path}`:\n\n"
        f"```bash\n{command}\n```\n\nAllow? (yes/no)"
    ]
    result = outputs["execute_file_command"]["value"]
    assert result.startswith("Overall: success (exit 0)")
    assert "one\nTWO\n" in result
    assert (tmp_path / "where.txt").read_text().strip() == str(tmp_path)


@pytest.mark.parametrize(
    "reply",
    [
        Str(value="no"),
        Str(value="sure, go ahead"),
        None,
        UserInputUnavailableError("no user"),
    ],
)
def test_command_does_not_run_without_an_explicit_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply
) -> None:
    prompts, outputs = _run(tmp_path, "touch marker", monkeypatch, reply)

    assert len(prompts) == 1
    assert outputs["guard_file_command"]["status"] == "denied"
    assert "execute_file_command" not in outputs
    assert not (tmp_path / "marker").exists()


def test_failed_command_reports_its_exit_status_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outputs = _run(
        tmp_path, "echo problem >&2; exit 3", monkeypatch, Str(value="y")
    )

    result = outputs["execute_file_command"]["value"]
    assert result.startswith("Overall: failure (exit 3)")
    assert "problem" in result


def test_prompt_fence_outlasts_backticks_in_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "echo '```'\necho hidden"
    prompts, _ = _run(tmp_path, command, monkeypatch, Str(value="no"))

    assert f"````bash\n{command}\n````\n" in prompts[0]


def test_capability_binds_the_command_to_the_sandbox_root(tmp_path: Path) -> None:
    definition = DeployableAgent(
        name="shell_command_test",
        system_prompt="Run the requested command.",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop), ShellCommands()),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path))
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    agent, _ = definition.build()

    tool = next(tool for tool in agent.tools if tool.name == "run_shell_command")
    assert f"Working directory: {tmp_path.resolve()}" in tool.description
