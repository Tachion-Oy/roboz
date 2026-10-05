"""Validate filesystem examples and execute command examples through the agent."""

import json
import re
import shutil

import pytest

from roboz import Agent
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, ApplyPatch, Operation, PermissionRule
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.skills import FilesystemContext, filesystem_skill
from roboz.shed.skills.filesystem.prompts import CLI_INSTRUCTIONS, PATCH_INSTRUCTIONS
from roboz.shed.tools.cli_commands import FileCommand
from roboz.tools import stop

EXAMPLES = {
    heading: json.loads(payload)
    for heading, payload in re.findall(
        r"### ([^\n]+)\n(?:(?!\n### ).)*?```json\n(.*?)\n```",
        CLI_INSTRUCTIONS,
        flags=re.DOTALL,
    )
}


def test_all_command_examples_use_the_public_input_contract():
    blocks = re.findall(r"```json\n(.*?)\n```", CLI_INSTRUCTIONS, re.DOTALL)
    assert blocks and len(EXAMPLES) == len(blocks)
    for block in blocks:
        request = FileCommand.model_validate(json.loads(block))
        assert request.value


def test_all_patch_examples_use_the_public_input_contract():
    blocks = re.findall(r"```json\n(.*?)\n```", PATCH_INSTRUCTIONS, re.DOTALL)
    assert blocks
    for block in blocks:
        request = ApplyPatch.model_validate(json.loads(block))
        assert request.path


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "report.txt").write_text("".join(f"line {i}\n" for i in range(1, 101)))
    (tmp_path / "source.txt").write_text("".join(f"source {i}\n" for i in range(1, 31)))
    (tmp_path / "primary.txt").write_text("primary marker\n")
    (tmp_path / "backup.txt").write_text("backup marker\n")
    (tmp_path / "fallback.txt").write_text("fallback marker\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("# TODO: example\n")
    return tmp_path


def _run(workspace, example, *, deny_rules=(), ask_rules=(), pipe=None):
    pipe = EventPipe() if pipe is None else pipe
    skill = filesystem_skill(
        FilesystemContext(
            permissions=PermissionPolicy(
                base=workspace,
                default_verdict=ActionVerdict.allow,
                deny=tuple(deny_rules),
                ask=tuple(ask_rules),
            ),
            pipe=pipe,
        )
    )
    agent = Agent(
        name="cli_examples",
        system_prompt="Run the documented example.",
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "run_file_command",
                    "rationale": "Example",
                    **EXAMPLES[example],
                },
                {"action": "stop", "rationale": "Done", "value": "done"},
            ]
        ),
        tools=[stop],
        auto_loaded_skills=[skill],
        event_pipe=pipe,
        initial_messages=None,
    )
    _, messages = agent.invoke()
    results = [
        json.loads(message.content) for message in messages if message.role == Role.USER
    ]
    assert any(
        item.get("caller") == skill.name and "# Instructions" in item.get("value", "")
        for item in results
    )
    return [
        item["value"]
        for item in results
        if item.get("caller") == "execute_file_command" and "value" in item
    ][-1]


@pytest.mark.parametrize("example", EXAMPLES)
def test_skill_example_executes_as_documented(workspace, example):
    if example == "Search with alternation" and not shutil.which("rg"):
        pytest.skip("Requires ripgrep")
    result = _run(workspace, example)
    assert result.startswith("Overall: success (exit 0)")
    match example:
        case "Dependent steps":
            assert (workspace / "notes/today.txt").read_text() == ""
        case "Fallback read" | "Skipped pipeline":
            assert "primary marker" in result and "backup marker" not in result
            assert "backup.txt" not in result
        case "Independent inspections":
            assert f"100 {workspace / 'report.txt'}" in result
            assert "line 1\n" in result and "line 100\n" in result
        case "Pipeline window":
            assert "line 61\n" in result and "line 80\n" in result
            assert "line 60\n" not in result and "line 81\n" not in result
        case "Mixed operators":
            expected = "".join(f"source {i}\n" for i in range(1, 21))
            assert (workspace / "out/preview.txt").read_text() == expected
            assert (
                f"30 {workspace / 'source.txt'}" in result
                and "fallback marker" not in result
            )
        case "Left-to-right conditions":
            assert (workspace / "preferred.txt").read_text() == (
                workspace / "source.txt"
            ).read_text()
            assert (workspace / "fallback.txt").read_text() == "fallback marker\n"
            assert "source 30\n" in result
        case "Fresh path expansion":
            assert (workspace / "out/new.txt").read_text() == (
                workspace / "source.txt"
            ).read_text()
            assert "source 30\n" in result
        case "Search with alternation":
            assert "src/app.py:1:# TODO: example" in result
        case "Find by name":
            assert "./src/app.py" in result
        case "Inline content":
            assert (workspace / "notes/today.txt").read_text() == "line 1\nline 2\n"
            assert "line 1\nline 2\n" in result
        case _:
            pytest.fail(f"Add behavioral expectations for {example}")


@pytest.mark.parametrize("failure", ["missing", "denied"])
def test_documented_fallback_is_independently_guarded(workspace, failure):
    if failure == "missing":
        (workspace / "primary.txt").unlink()
    denied = (
        [PermissionRule("primary.txt", {Operation.READ})] if failure == "denied" else []
    )
    result = _run(workspace, "Fallback read", deny_rules=denied)
    assert result.startswith("Overall: success") and "backup marker" in result
    result = _run(
        workspace,
        "Fallback read",
        deny_rules=[*denied, PermissionRule("backup.txt", {Operation.READ})],
    )
    assert result.startswith("Overall: failure") and "backup marker" not in result


@pytest.mark.parametrize("fallback_allowed", [True, False])
def test_documented_left_associativity_after_copy_denial(workspace, fallback_allowed):
    denied = [PermissionRule("preferred.txt", {Operation.CREATE})]
    if not fallback_allowed:
        denied.append(PermissionRule("fallback.txt", {Operation.CREATE}))
    result = _run(workspace, "Left-to-right conditions", deny_rules=denied)
    assert ("source 30\n" in result) is fallback_allowed
    assert not (workspace / "preferred.txt").exists()
    expected = (
        (workspace / "source.txt").read_text()
        if fallback_allowed
        else "fallback marker\n"
    )
    assert (workspace / "fallback.txt").read_text() == expected


def test_documented_mixed_chain_falls_back_then_continues(workspace):
    result = _run(
        workspace,
        "Mixed operators",
        deny_rules=[PermissionRule("out", {Operation.CREATE})],
    )
    assert result.startswith("Overall: success")
    assert "fallback marker" in result and f"30 {workspace / 'source.txt'}" in result
    assert not (workspace / "out").exists()


def test_documented_skipped_pipeline_never_requests_approval(workspace, monkeypatch):
    from roboz import runtime

    def unexpected_approval(*args, **kwargs):
        pytest.fail("A skipped pipeline must not request approval")

    monkeypatch.setattr(runtime, "interact_with_user", unexpected_approval)
    result = _run(
        workspace,
        "Skipped pipeline",
        pipe=EventPipe(),
        ask_rules=[PermissionRule("backup.txt", {Operation.READ})],
    )
    assert "primary marker" in result and "backup.txt" not in result
