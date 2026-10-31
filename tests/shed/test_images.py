"""Batch image reading uses the shared guards and preserves attachments once."""

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from roboz.models import render_content
from roboz import Agent
from roboz.exceptions import ExternalCallCancelledError
from roboz.agent import run_nested_agent
from roboz.llm import LLMEndpointRoute, MockLLMEndpoint
from roboz.models import Image, Role, Str
from roboz.runtime import EventPipe, bind_api_user_io, reset_api_user_io
from roboz.runtime.events import RuntimeEvent
from roboz.runtime.persistence import ConversationRun
from roboz.runtime.sinks import PersistenceSink
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.tools import ReadImagesInput, get_read_images_tool
from roboz.tools import stop


GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@pytest.fixture
def options(tmp_path):
    return PermissionPolicy.local(tmp_path).tool_options(EventPipe())


def invoke(options, paths, *, detail=None, tools=None):
    agent = Agent(
        name="image_reader", system_prompt="Inspect the images and stop.",
        tools=[*(tools if tools is not None else get_read_images_tool(**options)), stop],
        event_pipe=options.get("pipe"),
        agent_endpoint=MockLLMEndpoint([
            {"action": "read_images", "rationale": "Inspect", "paths": paths, "detail": detail},
            {"action": "stop", "rationale": "Done", "value": "done"},
        ], accepts_images=True),
    )
    _, messages = agent.invoke()
    result = next(
        message for message in reversed(messages)
        if json.loads(render_content(message, include_timestamp=False)).get("caller") == "execute_read_image"
    )
    return result, messages


def test_batch_delivers_ordered_images_once_and_persists_only_names(
    options, tmp_path,
):
    (tmp_path / "first.gif").write_bytes(GIF)
    png = b"\x89PNG\r\n\x1a\nimage bytes"
    (tmp_path / "second.png").write_bytes(png)
    sink = PersistenceSink.for_path(tmp_path / "conversations")
    options["pipe"].add_sink(sink)
    tools = get_read_images_tool(**options)
    result, messages = invoke(
        options, ["first.gif", str(tmp_path / "second.png"), "first.gif"],
        detail="high", tools=tools,
    )
    expected = [
        Image.from_bytes(data, media_type=media_type, name=name, detail="high")
        for data, media_type, name in [
            (GIF, "image/gif", "first.gif"), (png, "image/png", "second.png"),
            (GIF, "image/gif", "first.gif"),
        ]
    ]
    assert result.images == expected
    report = json.loads(result.content)["value"]
    assert "Read 3 of 3 images" in report
    assert "1. 'first.gif': Loaded as image 1" in report
    assert "3. 'first.gif': Loaded as image 3" in report
    assert [json.loads(m.content)["action"] for m in messages if m.role == Role.ASSISTANT] == [
        "read_images", "stop",
    ]
    assert [m for m in messages if m.images] == [result]
    assert all("base64," not in m.content for m in messages)
    assert sink.conversations_location is not None
    serialized = sink.conversations_location.read_text()
    persisted = ConversationRun.model_validate_json(serialized)
    assert [row.image_names for row in persisted.messages if row.image_names] == [
        ["first.gif", "second.png", "first.gif"],
    ]
    assert "base64," not in serialized
    assert all(image.url not in serialized for image in expected)


def test_reusing_tools_starts_a_fresh_batch(options, tmp_path):
    (tmp_path / "image.gif").write_bytes(GIF)
    tools = get_read_images_tool(**options)
    invoke(options, ["image.gif", "image.gif"], detail="high", tools=tools)
    fresh, _ = invoke(options, ["image.gif"], tools=tools)
    assert fresh.images == [Image.from_bytes(GIF, media_type="image/gif", name="image.gif")]
    assert "Read 1 of 1 images" in json.loads(fresh.content)["value"]


@pytest.mark.parametrize("data,media_type", [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff\xe0", "image/jpeg"),
    (b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
])
def test_format_uses_signature_and_keeps_original_bytes(options, tmp_path, data, media_type):
    # Signature identification deliberately does not decode image pixels.
    (tmp_path / "unknown.bin").write_bytes(data)
    result, _ = invoke(options, ["unknown.bin"])
    assert result.images == [Image.from_bytes(data, media_type=media_type, name="unknown.bin")]


def test_mixed_batch_does_not_open_denied_files_and_continues(tmp_path, monkeypatch):
    for name in ("allowed.gif", "denied.gif", "declined.gif", "unreadable.gif"):
        (tmp_path / name).write_bytes(GIF)
    (tmp_path / "invalid.png").write_text("not an image")
    options = PermissionPolicy(
        base=tmp_path,
        allow=(PermissionRule("**", {Operation.READ}),),
        deny=(PermissionRule("denied.gif", {Operation.READ}),),
        ask=(PermissionRule("*.gif", {Operation.READ}),),
    ).tool_options(EventPipe())
    original_read = Path.read_bytes
    opened = []

    def read_bytes(path):
        opened.append(path.name)
        if path.name == "unreadable.gif":
            raise PermissionError("File is unreadable")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with patch(
        "roboz.runtime.interact_with_user",
        side_effect=[Str(value=reply) for reply in ("no", "yes", "yes")],
    ) as prompt:
        result, _ = invoke(options, [
            "denied.gif", "declined.gif", "missing.gif", "invalid.png",
            "unreadable.gif", "allowed.gif",
        ])
    assert opened == ["invalid.png", "unreadable.gif", "allowed.gif"]
    assert prompt.call_count == 3
    assert result.images == [Image.from_bytes(GIF, media_type="image/gif", name="allowed.gif")]
    report = json.loads(result.content)["value"]
    assert "Read 1 of 6 images" in report
    assert "Denied read:" in report
    assert "Denied by user response" in report
    assert "existing regular file" in report
    assert "Unsupported image format" in report
    assert "File is unreadable" in report
    assert "6. 'allowed.gif': Loaded as image 1" in report


def test_all_denied_batch_returns_report_without_opening_files(tmp_path):
    (tmp_path / "image.gif").write_bytes(GIF)
    options = PermissionPolicy(base=tmp_path).tool_options(EventPipe())
    with patch.object(Path, "read_bytes", side_effect=AssertionError("Unauthorized read")):
        result, _ = invoke(options, ["image.gif"])
    assert result.images == []
    assert "Read 0 of 1 images" in json.loads(result.content)["value"]


@pytest.mark.parametrize("ask_for_approval", [False, True])
def test_standalone_reader_without_pipe_fails_closed_for_approvals(tmp_path, ask_for_approval):
    (tmp_path / "image.gif").write_bytes(GIF)
    options = {
        "base": tmp_path,
        "default_verdict": ActionVerdict.allow,
        "ask_rules": [PermissionRule("*.gif", {Operation.READ})] if ask_for_approval else [],
    }
    result, _ = invoke(options, ["image.gif"])
    if ask_for_approval:
        assert result.images == []
        assert "Approval unavailable" in json.loads(result.content)["value"]
    else:
        assert result.images == [Image.from_bytes(GIF, media_type="image/gif", name="image.gif")]


@pytest.mark.parametrize("path,expected", [
    ("*.gif", "not a glob"), ("link.gif", "Symlinks are unsupported"),
    ("folder", "existing regular file"), ("image.gif/", "trailing slash"),
])
def test_path_restrictions_report_failure_and_continue(options, tmp_path, path, expected):
    (tmp_path / "image.gif").write_bytes(GIF)
    (tmp_path / "link.gif").symlink_to(tmp_path / "image.gif")
    (tmp_path / "folder").mkdir()
    result, _ = invoke(options, [path, "image.gif"])
    assert expected in json.loads(result.content)["value"]
    assert result.images == [Image.from_bytes(GIF, media_type="image/gif", name="image.gif")]


def test_cancellation_between_images_prevents_the_next_read(options, tmp_path):
    (tmp_path / "image.gif").write_bytes(GIF)
    entry, guard, execute, continuation = get_read_images_tool(**options)
    prepared = entry(ReadImagesInput(paths=["image.gif", "image.gif"]), [])
    progress = execute(guard(prepared, []), [])
    options["pipe"].cancel()
    with patch.object(Path, "read_bytes", side_effect=AssertionError("Read after cancellation")):
        with pytest.raises(ExternalCallCancelledError):
            continuation(progress, [])


def test_input_schema_exposes_only_paths_and_shared_detail():
    properties = ReadImagesInput.model_json_schema()["properties"]
    assert set(properties) == {"paths", "detail"}
    assert properties["paths"]["minItems"] == 1
    for invalid in ({"paths": []}, {"paths": ["a"], "detail": "original"}, {"paths": [1]}):
        with pytest.raises(ValidationError):
            ReadImagesInput.model_validate(invalid)


@pytest.mark.parametrize("nested", [False, True])
def test_unsupported_reader_explains_and_next_text_turn_succeeds(tmp_path, monkeypatch, nested):
    events = []
    pipe = EventPipe(event_sinks=[events.append])
    endpoint = MockLLMEndpoint([
        {"action": "read_images", "rationale": "Inspect", "paths": ["missing.gif"]},
        {"action": "prompt_user", "rationale": "Continue", "value": "Continue with text?"},
        {"action": "stop", "rationale": "Done", "value": "text succeeded"},
    ])
    prompts = []

    class UserIO:
        def request_input(self, message, timeout=None, *, accepts_images=False):
            prompts.append(message)
            return Str(value="Keep chatting")

        def notify(self, message):
            raise AssertionError(message)

    def unexpected_read(path):
        raise AssertionError("Unsupported image request read a file")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    child = Agent(
        name="reader", system_prompt="Inspect and continue.", event_pipe=pipe,
        agent_endpoint=endpoint,
        tools=[*get_read_images_tool(base=tmp_path, default_verdict=ActionVerdict.allow, pipe=EventPipe(),
                                     ask_rules=[PermissionRule("**", {Operation.READ})]), stop],
    )
    root = Agent(
        name="parent", system_prompt="Delegate.",
        agent_endpoint=MockLLMEndpoint([
            {"action": "child", "rationale": "Delegate"},
            {"action": "stop", "rationale": "Done", "value": "done"},
        ], accepts_images=True),
        tools=[run_nested_agent(child).copy(name="child"), stop],
    ) if nested else child
    token = bind_api_user_io(UserIO())
    try:
        root.invoke()
    finally:
        reset_api_user_io(token)
    result = next(json.loads(m.content) for m in child.messages
                  if m.role == Role.USER and m.caller == "read_images")
    assert result["value"] == (
        f"The selected model, {endpoint.model_name}, cannot read images. "
        "Choose an image-capable model to inspect them. You can continue chatting with text."
    )
    assert prompts == ["Continue with text?"]
    assert all(not message.images for message in child.messages)
    assert "Keep chatting" in str([m.content for m in child.messages])
    assert not any(isinstance(e, RuntimeEvent) and e.kind in {"failed", "retrying"} for e in events)


@pytest.mark.parametrize("switch_at", ["permission", "second_permission", "read", "failed_read"])
def test_reader_rechecks_model_after_waits_and_before_attaching(tmp_path, monkeypatch, switch_at):
    source = tmp_path / "image.gif"
    source.write_bytes(GIF)
    vision = MockLLMEndpoint([
        {"action": "read_images", "rationale": "Inspect", "paths": ["image.gif", "image.gif"]},
    ], accepts_images=True)
    text = MockLLMEndpoint([{"action": "stop", "rationale": "Continue", "value": "ok"}])
    selected = vision
    reads = []
    prompts = []
    read_bytes = Path.read_bytes

    class UserIO:
        def request_input(self, message, timeout=None, *, accepts_images=False):
            nonlocal selected
            prompts.append(message)
            if switch_at == "permission" or (switch_at == "second_permission" and len(prompts) == 2):
                selected = text
            return Str(value="yes")

        def notify(self, message):
            raise AssertionError(message)

    def read(path):
        nonlocal selected
        reads.append(path)
        if switch_at == "read":
            selected = text
        if switch_at == "failed_read" and len(reads) == 2:
            selected = text
            raise OSError("Source became unreadable while the model changed")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    agent = Agent(
        name="reader", system_prompt="Inspect.",
        agent_endpoint=LLMEndpointRoute(lambda: selected),
        tools=[*get_read_images_tool(base=tmp_path, default_verdict=ActionVerdict.allow, pipe=EventPipe(),
                                     ask_rules=[PermissionRule("**", {Operation.READ})]), stop],
    )
    token = bind_api_user_io(UserIO())
    try:
        result, messages = agent.invoke()
    finally:
        reset_api_user_io(token)
    assert result.value == "ok"
    expected_reads = {"permission": 0, "second_permission": 1, "read": 1, "failed_read": 2}
    assert len(reads) == expected_reads[switch_at]
    assert all(not message.images for message in messages)
    assert any("cannot read images" in message.content for message in messages)
    assert read_bytes(source) == GIF
