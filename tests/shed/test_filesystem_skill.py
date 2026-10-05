"""The filesystem skill owns both guarded tool chains and their activation."""

import json

from roboz import Agent
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe
from roboz.shed.capabilities import Filesystem
from roboz.shed.sandbox import PermissionPolicy, Sandbox
from roboz.shed.skills import FilesystemContext, filesystem_skill
from roboz.tools import stop


def test_loading_filesystem_exposes_commands_and_patches_together(tmp_path):
    pipe = EventPipe()
    skill = filesystem_skill(FilesystemContext(PermissionPolicy.local(tmp_path), pipe))
    agent = Agent(
        name="files", system_prompt="Work on files.", event_pipe=pipe,
        tools=[stop], skills=[skill],
        agent_endpoint=MockLLMEndpoint([
            {"action": "filesystem", "rationale": "Load filesystem tools"},
            {
                "action": "apply_patch", "rationale": "Create a file",
                "path": "note.txt", "old_string": "", "new_string": "new content",
            },
            {
                "action": "run_file_command", "rationale": "Read the file",
                "value": [["cat", "CMD"], ["note.txt", "PTH"]],
            },
            {"action": "stop", "rationale": "Done", "value": "done"},
        ]),
    )
    assert not {"run_file_command", "apply_patch"} & {
        tool.name for tool in agent.active_tools.values()
    }
    assert "## File CLI" not in agent.full_system_prompt
    assert "## File editing" not in agent.full_system_prompt
    dependencies = agent.external_dependencies()
    assert any(dependency.dependency_id == "executable:cat" for dependency in dependencies)
    assert not agent.external_dependencies(include_lazy_skills=False)

    _, messages = agent.invoke()
    outputs = [json.loads(message.content) for message in messages if message.role.value == "user"]
    loaded = next(output["value"] for output in outputs if output.get("caller") == "filesystem")
    assert "## File CLI" in loaded and "## File editing" in loaded
    assert {"run_file_command", "apply_patch"} <= {
        tool.name for tool in agent.active_tools.values()
    }
    assert all(tool.chained_to for tool in agent.passive_tools.values())
    assert not any(tool.chained_to for tool in agent.active_tools.values())
    assert "new content" in next(
        output["value"] for output in outputs if output.get("caller") == "execute_file_command"
    )
    assert (tmp_path / "note.txt").read_text() == "new content"


def test_filesystem_capability_keeps_tools_and_guidance_for_on_demand_loading(tmp_path):
    definition = DeployableAgent(
        name="files", system_prompt="Work on files.",
        default_capabilities=(Capability(tools=(stop,)), Filesystem(auto_load_skill=False)),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path, scope="project"))
    definition.set_agent_endpoint(MockLLMEndpoint([
        {"action": "filesystem", "rationale": "Load filesystem tools"},
        {
            "action": "apply_patch", "rationale": "Create a file",
            "path": "projects/project/note.txt", "old_string": "", "new_string": "content",
        },
        {
            "action": "run_file_command", "rationale": "Read the file",
            "value": [["cat", "CMD"], ["projects/project/note.txt", "PTH"]],
        },
        {"action": "stop", "rationale": "Done", "value": "done"},
    ]))
    agent, _ = definition.build()
    assert not {"run_file_command", "apply_patch"} & {
        tool.name for tool in agent.active_tools.values()
    }
    assert [skill.name for skill in agent.skills] == ["filesystem"]
    assert not agent.auto_loaded_skills

    _, messages = agent.invoke()
    outputs = [json.loads(message.content) for message in messages if message.role.value == "user"]
    loaded = next(output["value"] for output in outputs if output.get("caller") == "filesystem")
    assert "## File CLI" in loaded and "## File editing" in loaded
    assert {"run_file_command", "apply_patch"} <= {
        tool.name for tool in agent.active_tools.values()
    }
    assert "content" in next(
        output["value"] for output in outputs if output.get("caller") == "execute_file_command"
    )
    assert (tmp_path / "projects/project/note.txt").read_text() == "content"
