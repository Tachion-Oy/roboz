"""The filesystem skill owns its guarded tool chains and their activation."""

import base64
import json

import pytest

from roboz.models import render_content
from roboz import Agent
from roboz.deployment import (
    Capability,
    DeployableAgent,
    SkillLabel,
    ToolLabel,
)
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
        name="files",
        system_prompt="Work on files.",
        event_pipe=pipe,
        tools=[stop],
        skills=[skill],
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "filesystem", "rationale": "Load filesystem tools"},
                {
                    "action": "apply_patch",
                    "rationale": "Create a file",
                    "path": "note.txt",
                    "old_string": "",
                    "new_string": "new content",
                },
                {
                    "action": "run_file_command",
                    "rationale": "Read the file",
                    "value": [["cat", "CMD"], ["note.txt", "PTH"]],
                },
                {"action": "stop", "rationale": "Done", "value": "done"},
            ]
        ),
    )
    assert not {"run_file_command", "apply_patch", "read_images"} & {
        tool.name for tool in agent.active_tools.values()
    }
    assert "## File CLI" not in agent.full_system_prompt
    assert "## File editing" not in agent.full_system_prompt
    assert "## Image reading" not in agent.full_system_prompt
    dependencies = agent.external_dependencies()
    assert any(
        dependency.dependency_id == "executable:cat" for dependency in dependencies
    )
    assert not agent.external_dependencies(include_lazy_skills=False)

    _, messages = agent.invoke()
    outputs = [
        json.loads(render_content(message))
        for message in messages
        if message.role.value == "user"
    ]
    loaded = next(
        output["value"] for output in outputs if output.get("caller") == "filesystem"
    )
    assert "## File CLI" in loaded and "## File editing" in loaded
    assert "## Image reading" in loaded
    assert {"run_file_command", "apply_patch", "read_images"} <= {
        tool.name for tool in agent.active_tools.values()
    }
    assert all(tool.chained_to for tool in agent.passive_tools.values())
    assert not any(tool.chained_to for tool in agent.active_tools.values())
    assert "new content" in next(
        output["value"]
        for output in outputs
        if output.get("caller") == "execute_file_command"
    )
    assert (tmp_path / "note.txt").read_text() == "new content"


@pytest.mark.parametrize("on_demand", [False, True])
def test_filesystem_capability_keeps_tools_and_guidance(tmp_path, on_demand):
    image_bytes = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    (tmp_path / "image.gif").write_bytes(image_bytes)
    definition = DeployableAgent(
        name="files",
        system_prompt="Work on files.",
        capabilities=(
            Capability(label=ToolLabel("stop"), value=stop),
            Filesystem(label=SkillLabel("filesystem")) if on_demand else Filesystem(),
        ),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path, scope="project"))
    definition.set_agent_endpoint(
        MockLLMEndpoint(
            [
                *([{"action": "filesystem", "rationale": "Load filesystem tools"}]
                  if on_demand else []),
                {
                    "action": "apply_patch",
                    "rationale": "Create a file",
                    "path": "projects/project/note.txt",
                    "old_string": "",
                    "new_string": "content",
                },
                {
                    "action": "run_file_command",
                    "rationale": "Read the file",
                    "value": [["cat", "CMD"], ["projects/project/note.txt", "PTH"]],
                },
                {"action": "read_images", "rationale": "Inspect", "paths": ["image.gif"]},
                {"action": "stop", "rationale": "Done", "value": "done"},
            ],
            accepts_images=True,
        )
    )
    agent, _ = definition.build()
    if on_demand:
        assert not {"run_file_command", "apply_patch", "read_images"} & {
            tool.name for tool in agent.active_tools.values()
        }
        assert [skill.name for skill in agent.skills] == ["filesystem"]
        assert not agent.auto_loaded_skills
    else:
        assert [skill.name for skill in agent.auto_loaded_skills] == ["filesystem"]

    _, messages = agent.invoke()
    outputs = [
        json.loads(render_content(message))
        for message in messages
        if message.role.value == "user"
    ]
    if on_demand:
        loaded = next(
            output["value"] for output in outputs if output.get("caller") == "filesystem"
        )
        assert "## File CLI" in loaded and "## File editing" in loaded
        assert "## Image reading" in loaded
    assert {"run_file_command", "apply_patch", "read_images"} <= {
        tool.name for tool in agent.active_tools.values()
    }
    assert "content" in next(
        output["value"]
        for output in outputs
        if output.get("caller") == "execute_file_command"
    )
    assert (tmp_path / "projects/project/note.txt").read_text() == "content"
    images = [image for message in messages for image in message.images]
    assert len(images) == 1
    assert base64.b64decode(images[0].url.partition(",")[2]) == image_bytes
