"""Tests for threshold-triggered in-place message compactification."""

import json
from typing import Any

import pytest
from roboz_shed.identifiers import COMPACTIFY_MESSAGES_TOOL_NAME
from roboz_shed.tools.compactification import (
    COMPACTED_CONTEXT_KIND,
    COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    COMPACTIFY_SYSTEM_PROMPT,
    CompactifyStatus,
    compactify_messages_when_needed,
    get_compactify_messages_when_needed_tool,
)

from roboz import All, Ctx, Message, Role
from roboz.llm import MockLLMEndpoint
from roboz.models import MessageKind
from roboz.models.truncation import Severity


def _ctx(**overrides) -> Ctx:
    values = dict(
        endpoint=MockLLMEndpoint([{"value": "# compact"}], max_context_tokens=1_000),
        threshold_percent=80.0,
        system_prompt=COMPACTIFY_SYSTEM_PROMPT,
        skill_message=COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    )
    return Ctx(**(values | overrides))


def test_compactify_messages_skips_below_threshold() -> None:
    messages = [
        Message(
            role=Role.SYSTEM, content="sys", message_kind=MessageKind.SYSTEM_MESSAGE
        ),
        Message(role=Role.USER, content="a" * 400),  # 100 tokens => 10%
    ]
    initial = list(messages)
    out = compactify_messages_when_needed(_ctx())(input=All(), messages=messages)
    assert isinstance(out, CompactifyStatus)
    assert out.status == "ok"
    assert out.compactions == 0
    assert out.compaction_summary is None
    assert messages == initial
    if isinstance(out.truncation, list):
        severity = out.truncation[0].severity
    else:
        severity = out.truncation.severity
    assert severity == Severity.REMOVE


def test_compactify_messages_rewrites_list_in_place_above_threshold() -> None:
    messages = [
        Message(
            role=Role.SYSTEM, content="sys", message_kind=MessageKind.SYSTEM_MESSAGE
        ),
        Message(role=Role.USER, content="u" * 3200),  # 800 tokens => 80%
        Message(role=Role.ASSISTANT, content='{"action":"x","rationale":"y"}'),
    ]
    original_list_id = id(messages)
    out = compactify_messages_when_needed(_ctx())(input=All(), messages=messages)

    assert isinstance(out, CompactifyStatus)
    assert out.status == "compacted"
    assert out.compactions == 1
    assert id(messages) == original_list_id
    assert len(messages) == 2
    assert messages[0].role == Role.SYSTEM
    assert messages[0].content == "sys"
    assert messages[1].message_kind == COMPACTED_CONTEXT_KIND
    compacted = json.loads(messages[1].content)
    assert compacted["caller"] == COMPACTIFY_MESSAGES_TOOL_NAME
    assert "kind" not in compacted
    assert "# compact" in compacted["summary_markdown"]

    # The in-place message carries the summary into agent context; the tool's
    # returned status carries it into the persisted event pipe (see
    # get_finalized_message + Agent.append_and_pipe), since only the return
    # value is ever piped to disk, never in-place list mutations.
    assert out.compaction_summary == compacted["summary_markdown"]


def test_compactify_messages_preserves_bootstrap_prefix_without_skill_args() -> None:
    ctx = _ctx()
    startup_payload = '{"value":"# Prior memory\\nlong context"}'
    banner_payload = '{"caller":"prompt_user","value":"skills auto-loaded"}'
    skill_payload = '{"caller":"auto","value":"# Instructions\\n\\nSkill instructions"}'
    depends_on_skill_payload = '{"caller":"file_editing","value":"# Important Note\\n\\nDependency note\\n\\n# Instructions\\n\\nSkill instructions"}'
    messages = [
        Message(
            role=Role.SYSTEM, content="sys", message_kind=MessageKind.SYSTEM_MESSAGE
        ),
        Message(
            role=Role.USER,
            content=startup_payload,
            message_kind=MessageKind.STARTUP_CONTEXT,
        ),
        Message(
            role=Role.USER,
            content=banner_payload,
            message_kind=MessageKind.AUTO_LOAD_BANNER,
        ),
        Message(
            role=Role.USER,
            content=skill_payload,
            message_kind=MessageKind.AUTO_LOADED_SKILL,
        ),
        Message(
            role=Role.USER,
            content=depends_on_skill_payload,
            message_kind=MessageKind.AUTO_LOADED_SKILL,
        ),
        Message(role=Role.USER, content="u" * 3200),
    ]

    out = compactify_messages_when_needed(ctx)(input=All(), messages=messages)

    assert out.status == "compacted"
    assert [m.content for m in messages[:5]] == [
        "sys",
        startup_payload,
        banner_payload,
        skill_payload,
        depends_on_skill_payload,
    ]
    assert messages[5].message_kind == COMPACTED_CONTEXT_KIND
    compacted = json.loads(messages[5].content)
    assert "kind" not in compacted


def test_compactify_messages_rejects_non_positive_threshold() -> None:
    ctx = _ctx(threshold_percent=0.0)
    with pytest.raises(ValueError, match="threshold_percent"):
        compactify_messages_when_needed(ctx)(input=All(), messages=[])

    with pytest.raises(ValueError, match="threshold_percent"):
        get_compactify_messages_when_needed_tool(
            endpoint=MockLLMEndpoint([]),
            threshold_percent=0.0,
        )


def test_compactify_messages_rejects_callable_endpoint() -> None:
    build: Any = get_compactify_messages_when_needed_tool
    with pytest.raises(TypeError, match="endpoint"):
        build(
            endpoint=lambda: MockLLMEndpoint([]),
            threshold_percent=80.0,
        )


def test_compactify_messages_rejects_blank_skill_message() -> None:
    messages = [
        Message(
            role=Role.SYSTEM, content="sys", message_kind=MessageKind.SYSTEM_MESSAGE
        ),
        Message(role=Role.USER, content="u" * 3200),
    ]
    ctx = _ctx(skill_message=" ")

    with pytest.raises(ValueError, match="skill_message"):
        compactify_messages_when_needed(ctx)(input=All(), messages=messages)


def test_second_compaction_folds_prior_summary_and_keeps_bootstrap() -> None:
    ctx = _ctx(
        endpoint=MockLLMEndpoint(
            [{"value": "# compact-1"}, {"value": "# compact-2"}],
            max_context_tokens=1_000,
        )
    )
    messages = [
        Message(
            role=Role.SYSTEM, content="sys", message_kind=MessageKind.SYSTEM_MESSAGE
        ),
        Message(
            role=Role.USER,
            content='{"value":"# startup"}',
            message_kind=MessageKind.STARTUP_CONTEXT,
        ),
        Message(role=Role.USER, content="u" * 3200),
    ]

    first = compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert first.status == "compacted"
    assert len(messages) == 3
    assert messages[2].message_kind == COMPACTED_CONTEXT_KIND

    # Add more dynamic tail and compact again; previous compacted summary should fold.
    messages.append(Message(role=Role.USER, content="v" * 3200))
    second = compactify_messages_when_needed(ctx)(input=All(), messages=messages)
    assert second.status == "compacted"
    assert [m.message_kind for m in messages[:2]] == [
        MessageKind.SYSTEM_MESSAGE,
        MessageKind.STARTUP_CONTEXT,
    ]
    assert len(messages) == 3
    assert messages[2].message_kind == COMPACTED_CONTEXT_KIND


def test_compactify_prompts_do_not_reference_prior_snapshots() -> None:
    compactify_prompt = COMPACTIFY_SYSTEM_PROMPT.lower()
    skill_prompt = COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE.lower()

    for banned in (
        "prior compacted continuation snapshot",
        "prior compacted snapshot",
        "previous compacted continuation snapshot",
        "delta from previous snapshot",
    ):
        assert banned not in compactify_prompt
        assert banned not in skill_prompt

    assert "response's `value` field" in COMPACTIFY_SYSTEM_PROMPT
