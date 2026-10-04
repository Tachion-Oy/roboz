"""Permission replies reach the next model request through ordinary tool output."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from roboz import Agent
from roboz.llm import LLMEndpoint, MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe, bind_api_user_io, reset_api_user_io
from roboz.shed.models import ActionVerdict, ApplyPatch, Operation, PermissionRule
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools import get_apply_patch, get_run_file_command
from roboz.shed.tools.cli_commands import FileCommand
from roboz.shed.tools.cli_commands.constants import MAX_COMMAND_OUTPUT_CHARS
from roboz.tools import stop


@pytest.fixture
def replies():
    bindings = []

    def bind(*responses):
        remaining = iter(responses)
        prompts = []

        def request_input(message, timeout=None):
            prompts.append(message)
            reply = next(remaining)
            if isinstance(reply, Exception):
                raise reply
            return reply

        bindings.append(bind_api_user_io(SimpleNamespace(request_input=request_input)))
        return prompts

    yield bind
    for token in reversed(bindings):
        reset_api_user_io(token)


@pytest.mark.parametrize(
    "last_reply",
    [
        " \tYeS\n",
        'no, use "draft".\nPlease keep café.',
        None,
        RuntimeError("UI disconnected"),
    ],
)
def test_next_model_request_contains_replies_after_chaining_and_piping(
    tmp_path: Path, replies, last_reply
):
    sandbox = Sandbox(tmp_path, shared="workspace", scope="testing")
    sandbox.project_dir().mkdir(parents=True)
    sandbox.shared_dir.mkdir()
    prompts = replies("yes", last_reply)
    tokens = [
        ("touch", "CMD"),
        ("projects/testing/seed", "PTH"),
        ("&&", "CTL"),
        ("tee", "CMD"),
        ("abc", "ARG"),
        ("workspace/one", "PTH"),
        ("workspace/two", "PTH"),
        ("|", "CTL"),
        ("wc", "CMD"),
        ("-c", "FLG"),
        (";", "CTL"),
        ("pwd", "CMD"),
    ]
    requests = []
    responses = iter(
        [
            {"action": "run_file_command", "rationale": "Write", "value": tokens},
            {"action": "stop", "rationale": "Done", "value": "done"},
        ]
    )

    def create(**kwargs):
        requests.append(kwargs["messages"])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(next(responses)))
                )
            ],
            usage=None,
        )

    endpoint = LLMEndpoint(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            models=object(),
            close=lambda: None,
        ),
        api_name="test",
        model_name="test",
        stream=False,
    )
    agent = Agent(
        name="approval_test",
        system_prompt="Write files.",
        agent_endpoint=endpoint,
        tools=[
            *get_run_file_command(**sandbox.permissions().tool_options(EventPipe())),
            stop,
        ],
        initial_messages=None,
    )
    agent.invoke()

    assert len(requests) == 2
    outputs = [json.loads(m["content"]) for m in requests[1] if m["role"] == Role.USER]
    assert not any(
        m.get("caller") == "guard_file_command" and m.get("status") == "allowed"
        for m in outputs
    )
    report = next(
        m["value"]
        for m in outputs
        if m.get("caller") == "execute_file_command" and "value" in m
    )
    assert len(prompts) == 2
    assert report.count("Permission prompt:") == 2
    assert report.index(prompts[0]) < report.index(prompts[1])
    assert 'User reply: "yes"\nDecision: allowed' in report
    allowed = last_reply == " \tYeS\n"
    if isinstance(last_reply, str):
        assert f"User reply: {json.dumps(last_reply, ensure_ascii=False)}" in report
        assert f"Decision: {'allowed' if allowed else 'denied'}" in report
    elif last_reply is None:
        assert "User reply: (no reply received)\nDecision: denied" in report
        assert "Denied: no reply received to permission prompt." in report
    else:
        assert "Approval unavailable: UI disconnected" in report
        assert report.count("User reply:") == 1
    assert f"\n{3 if allowed else 0}\n--- end: wc -c ---" in report
    assert (sandbox.project_dir() / "seed").exists()
    for name in ("one", "two"):
        target = sandbox.shared_dir / name
        assert target.exists() is allowed
        if allowed:
            assert target.read_text() == "abc"


@pytest.mark.parametrize("failure", ["native", "exception", "output_limit"])
def test_approved_cli_failure_retains_user_reply(
    tmp_path, replies, monkeypatch, failure
):
    prompts = replies(" Y ")

    def run(argv, **kwargs):
        if failure == "exception":
            raise OSError("execution failed")
        if failure == "native":
            return subprocess.CompletedProcess(argv, 1, b"", b"native failure")
        return subprocess.CompletedProcess(
            argv, 0, b"x" * (MAX_COMMAND_OUTPUT_CHARS + 1), b""
        )

    monkeypatch.setattr(subprocess, "run", run)
    entry, guard, execute, _ = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule("note", {Operation.CREATE})],
        pipe=EventPipe(),
    )
    result = execute(
        guard(entry(FileCommand(value=[("tee", "CMD"), ("note", "PTH")]), []), []), []
    )
    assert result.value.startswith("Overall: failure")
    assert prompts[0] in result.value
    assert 'User reply: " Y "\nDecision: allowed' in result.value


@pytest.mark.parametrize("old", ["before", "missing"])
def test_patch_report_retains_approval_on_success_and_failure(tmp_path, replies, old):
    target = tmp_path / "note"
    target.write_text("before")
    prompts = replies(" Yes ")
    entry, guard, execute = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule("note", {Operation.DELETE})],
        pipe=EventPipe(),
    )
    result = execute(
        guard(
            entry(ApplyPatch(path="note", old_string=old, new_string="after"), []), []
        ),
        [],
    )
    assert prompts[0] in result.value
    assert 'User reply: " Yes "\nDecision: allowed' in result.value
    assert target.read_text() == ("after" if old == "before" else "before")


def test_explicit_private_read_denial_allows_move_without_read(tmp_path):
    sandbox = Sandbox(tmp_path, scope="testing")
    private = sandbox.project_dir() / "private"
    private.mkdir(parents=True)
    (private / "secret").write_text("PRIVATE-MARKER")
    options = sandbox.permissions().tool_options(EventPipe())
    options.update(
        deny_rules=[PermissionRule("projects/testing/private/**", {Operation.READ})],
        takes_precedence=ActionVerdict.deny,
    )
    tokens = [
        ("cat", "CMD"),
        ("projects/testing/private/./secret", "PTH"),
        (";", "CTL"),
        ("mv", "CMD"),
        ("projects/testing/private/secret", "PTH"),
        ("projects/testing/moved", "PTH"),
    ]
    agent = Agent(
        name="private_test",
        system_prompt="Try reading, then move.",
        tools=[*get_run_file_command(**options), stop],
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "run_file_command", "rationale": "Check", "value": tokens},
                {"action": "stop", "rationale": "Done", "value": "done"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    outputs = [json.loads(m.content) for m in messages if m.role == Role.USER]
    report = next(
        m["value"]
        for m in outputs
        if m.get("caller") == "execute_file_command" and "value" in m
    )
    assert "Denied read:" in report and "PRIVATE-MARKER" not in report
    assert not (private / "secret").exists()
    assert (sandbox.project_dir() / "moved").read_text() == "PRIVATE-MARKER"
