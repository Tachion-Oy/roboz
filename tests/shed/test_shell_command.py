"""Arbitrary Bash commands run only after the user approves each one."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.deployment import Capability, DeployableAgent, ToolLabel
from roboz.exceptions import UserInputUnavailableError
from roboz.llm import MockLLMEndpoint
from roboz.llm._truncation import get_truncated_messages_for_context
from roboz.models import Role, Str, render_content
from roboz.runtime import EventPipe, ScriptOutputEvent
from roboz.shed.capabilities import ShellCommands
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools import ExecutionPolicy, get_run_shell_command
from roboz.tools import stop

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="shell commands require POSIX and Bash",
)


def _run(
    base: Path,
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    reply,
    *,
    pipe: EventPipe | None = None,
    policy: ExecutionPolicy = ExecutionPolicy(),
) -> tuple:
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
        tools=[
            *get_run_shell_command(base=base, pipe=pipe or EventPipe(), policy=policy),
            stop,
        ],
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
    result = outputs["execute_shell_command"]["value"]
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
    assert "execute_shell_command" not in outputs
    assert not (tmp_path / "marker").exists()


def test_declined_command_leaves_only_its_denial_in_the_model_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime, "interact_with_user", lambda *args, **kwargs: Str(value="no")
    )
    agent = Agent(
        name="shell_context_test",
        system_prompt="Run the requested command.",
        tools=[*get_run_shell_command(base=tmp_path, pipe=EventPipe()), stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {"action": "run_shell_command", "rationale": "Run", "command": "true"},
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()

    context = [
        json.loads(render_content(message))
        for message in get_truncated_messages_for_context(messages)
        if message.role == Role.USER and message.caller != "stop"
    ]
    assert [entry["caller"] for entry in context] == ["guard_file_command"]
    assert context[0]["deny_reason"] == "user_declined"


def test_failed_command_reports_its_exit_status_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outputs = _run(
        tmp_path, "echo problem >&2; exit 3", monkeypatch, Str(value="y")
    )

    result = outputs["execute_shell_command"]["value"]
    assert result.startswith("Overall: failure (exit 3)")
    assert "problem" in result


def test_command_sees_the_path_and_only_the_allowed_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL_COMMAND_TEST_HIDDEN", "secret")
    monkeypatch.setenv("SHELL_COMMAND_TEST_ALLOWED", "visible")
    command = (
        'echo "${SHELL_COMMAND_TEST_ALLOWED:-missing}|'
        '${SHELL_COMMAND_TEST_HIDDEN:-missing}|$PATH"'
    )
    _, outputs = _run(
        tmp_path,
        command,
        monkeypatch,
        Str(value="y"),
        policy=ExecutionPolicy(env_allowlist=("SHELL_COMMAND_TEST_ALLOWED",)),
    )

    result = outputs["execute_shell_command"]["value"]
    assert f"visible|missing|{os.environ['PATH']}\n" in result


def test_output_reaches_the_user_and_silence_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list = []
    pipe = EventPipe(event_sinks=[observed.append])
    _, outputs = _run(tmp_path, "echo shown", monkeypatch, Str(value="y"), pipe=pipe)
    _, silent = _run(tmp_path, "true", monkeypatch, Str(value="y"))

    shown = [event.content for event in observed if isinstance(event, ScriptOutputEvent)]
    assert "".join(shown) == "shown\n"
    assert 'User reply: "y"' in outputs["execute_shell_command"]["value"]
    assert "completed with no output" in silent["execute_shell_command"]["value"]


def test_time_limit_ends_the_command_and_what_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "(sleep 0.5; echo leaked > child-marker.txt) &\nwait"
    _, outputs = _run(
        tmp_path,
        command,
        monkeypatch,
        Str(value="y"),
        policy=ExecutionPolicy(timeout_s=0.1),
    )

    result = outputs["execute_shell_command"]["value"]
    assert result.startswith("Overall: failure (timeout)")
    time.sleep(0.6)
    assert not (tmp_path / "child-marker.txt").exists()


def test_cancelling_the_run_ends_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipe = EventPipe()

    def cancel_on_output(event) -> None:
        if isinstance(event, ScriptOutputEvent):
            pipe.cancel()

    pipe.add_sink(cancel_on_output)
    started = time.monotonic()
    command = "echo ready; sleep 10; touch finished"
    _, outputs = _run(tmp_path, command, monkeypatch, Str(value="y"), pipe=pipe)

    assert time.monotonic() - started < 5
    assert "execute_shell_command" not in outputs
    assert not (tmp_path / "finished").exists()


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
    assert "Time limit: 300 seconds" in tool.description
