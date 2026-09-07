"""Unit tests for truncation: select_truncation, get_messages_for_context, EventPipe.

Structure:
1. select_truncation - unit tests for the selection helper (single vs list)
2. get_messages_for_context - integration via tool output -> message flow
3. EventPipe - truncation on save when threshold is 0
"""

import json
from pathlib import Path

import pytest

from roboz import Ctx
from roboz.llm._truncation import (
    TRUNCATED_PLACEHOLDER,
    get_truncated_messages_for_context,
    select_truncation,
)
from roboz.models import (
    LIGHT_MAX_CHARS,
    Empty,
    Invoke,
    LocationStr,
    Message,
    Role,
    Stop,
)
from roboz.models._serialization import get_finalized_message
from roboz.models.truncation import (
    DEFAULT,
    GRADED,
    GRADED_REMOVE_DISTANCE,
    GRADED_STUB_DISTANCE,
    NO_TRUNCATION,
    Severity,
    Truncation,
    TruncationSpec,
)
from roboz.runtime.pipe import EventPipe
from roboz.runtime.sinks import PersistenceSink
from roboz.tooling.decorators import factory

# ---------- select_truncation (unit tests) ----------


@pytest.mark.parametrize(
    "distance,rules,expected",
    [
        # Single rule, negative threshold -> no truncation
        (5, Truncation(threshold=-1, severity=Severity.LIGHT), None),
        # Single rule, threshold applies
        (
            5,
            Truncation(threshold=4, severity=Severity.STUB),
            Truncation(threshold=4, severity=Severity.STUB),
        ),
        (
            3,
            Truncation(threshold=4, severity=Severity.STUB),
            None,
        ),  # distance < threshold
        # Graded list: pick max applicable threshold
        (
            7,
            [
                Truncation(threshold=0, severity=Severity.LIGHT),
                Truncation(threshold=4, severity=Severity.STUB),
                Truncation(threshold=10, severity=Severity.REMOVE),
            ],
            Truncation(threshold=4, severity=Severity.STUB),
        ),
        (
            10,
            [
                Truncation(threshold=0, severity=Severity.LIGHT),
                Truncation(threshold=4, severity=Severity.STUB),
                Truncation(threshold=10, severity=Severity.REMOVE),
            ],
            Truncation(threshold=10, severity=Severity.REMOVE),
        ),
        (
            1,
            [
                Truncation(threshold=1, severity=Severity.STUB),
                Truncation(threshold=1, severity=Severity.LIGHT),
                Truncation(threshold=1, severity=Severity.REMOVE),
            ],
            Truncation(threshold=1, severity=Severity.REMOVE),
        ),
    ],
)
def test_select_truncation(
    distance: int,
    rules: Truncation | list,
    expected: Truncation | None,
) -> None:
    assert select_truncation(distance, rules) == expected


# ---------- Truncation test factory (for integration tests) ----------


@factory
def truncation_test(
    input: Empty, messages: list[Message], ctx: Ctx
) -> Invoke | LocationStr | Stop:
    """Factory: returns output with configurable truncation from context."""
    threshold = ctx.threshold
    severity = ctx.severity
    output_class = ctx.output_class
    truncation = Truncation(threshold=threshold, severity=severity)
    if output_class is Invoke:
        return Invoke(
            action="truncation_test",
            rationale="x" * LIGHT_MAX_CHARS * 2,
            truncation=truncation,
        )
    if output_class is LocationStr:
        return LocationStr(
            location=Path("."),
            value="y" * LIGHT_MAX_CHARS * 2,
            truncation=truncation,
        )
    return Stop(value="z" * LIGHT_MAX_CHARS * 2, truncation=truncation)


# ---------- Fixtures ----------


@pytest.fixture
def pipe() -> EventPipe:
    """Fresh EventPipe for each test; output is not CLI so __call__ skips Rich."""
    return EventPipe()


def _long_marker(output_class: type) -> str:
    """Return the 100-char substring used for 'full content' assertions."""
    if output_class is Invoke:
        return "x" * 100
    if output_class is LocationStr:
        return "y" * 100
    return "z" * 100


# ---------- get_messages_for_context: parametrized ----------


@pytest.mark.parametrize(
    "ctx,num_messages,expected",
    [
        (
            Ctx(threshold=-1, severity=Severity.LIGHT, output_class=Invoke),
            3,
            "all_full",
        ),
        (
            Ctx(threshold=0, severity=Severity.STUB, output_class=Invoke),
            2,
            "all_truncated",
        ),
        (
            Ctx(threshold=1, severity=Severity.STUB, output_class=Invoke),
            3,
            "last_full",
        ),
        (
            Ctx(threshold=2, severity=Severity.LIGHT, output_class=Invoke),
            4,
            "last_two_full",
        ),
        (
            Ctx(threshold=0, severity=Severity.STUB, output_class=LocationStr),
            1,
            "caller_stub",
        ),
        (
            Ctx(threshold=1, severity=Severity.STUB, output_class=LocationStr),
            2,
            "last_full",
        ),
        (
            Ctx(threshold=0, severity=Severity.STUB, output_class=Stop),
            1,
            "caller_stub",
        ),
        (
            Ctx(threshold=1, severity=Severity.STUB, output_class=Stop),
            2,
            "last_full",
        ),
    ],
)
def test_get_messages_for_context_parametrized(
    ctx: Ctx, num_messages: int, expected: str
) -> None:
    tool = truncation_test(ctx)
    messages: list[Message] = [
        get_finalized_message(tool(input=Empty(), messages=[]), tool)
        for _ in range(num_messages)
    ]
    result = get_truncated_messages_for_context(messages)
    output_class = ctx.output_class
    marker = _long_marker(output_class)

    if expected == "all_full":
        assert len(result) == num_messages
        for m in result:
            assert marker in m.content
            assert TRUNCATED_PLACEHOLDER not in m.content
    elif expected == "all_truncated":
        assert len(result) == num_messages
        for m in result:
            assert TRUNCATED_PLACEHOLDER in m.content
            assert marker not in m.content
    elif expected == "last_full":
        assert len(result) == num_messages
        assert marker in result[-1].content
        assert TRUNCATED_PLACEHOLDER not in result[-1].content
        for m in result[:-1]:
            assert TRUNCATED_PLACEHOLDER in m.content
    elif expected == "last_two_full":
        assert len(result) == num_messages
        assert marker in result[-2].content
        assert marker in result[-1].content
        for m in result[:-2]:
            assert "..." in m.content
    elif expected == "caller_stub":
        assert len(result) == 1
        m = result[0]
        assert TRUNCATED_PLACEHOLDER in m.content
        assert '"caller"' in m.content
        assert marker not in m.content
    else:
        pytest.fail(f"Unknown expected: {expected}")


# ---------- get_messages_for_context: standalone ----------


def test_get_messages_for_context_remove() -> None:
    """Mixed ctx: (1, remove) + (-1, light) x2; assert 2 msgs, m1 omitted."""
    ctx_remove = Ctx(threshold=1, severity=Severity.REMOVE, output_class=Invoke)
    ctx_keep = Ctx(threshold=-1, severity=Severity.LIGHT, output_class=Invoke)
    tool_del = truncation_test(ctx_remove)
    tool_keep = truncation_test(ctx_keep)
    m1 = get_finalized_message(tool_del(input=Empty(), messages=[]), tool_del)
    m2 = get_finalized_message(tool_keep(input=Empty(), messages=[]), tool_keep)
    m3 = get_finalized_message(tool_keep(input=Empty(), messages=[]), tool_keep)
    messages = [m1, m2, m3]
    result = get_truncated_messages_for_context(messages)
    assert len(result) == 2
    assert result[0].content == m2.content
    assert result[1].content == m3.content


def test_pathstr_light() -> None:
    """(0, light, PathStr); assert value truncated to ~200 chars."""
    ctx = Ctx(threshold=0, severity=Severity.LIGHT, output_class=LocationStr)
    tool = truncation_test(ctx)
    messages = [get_finalized_message(tool(input=Empty(), messages=[]), tool)]
    result = get_truncated_messages_for_context(messages)
    assert len(result) == 1
    data = json.loads(result[0].content)
    assert "..." in data.get("value", "")
    assert len(data.get("value", "")) <= LIGHT_MAX_CHARS + 20


def test_empty_messages() -> None:
    """get_messages_for_context([]) returns []."""
    assert get_truncated_messages_for_context([]) == []


def test_get_messages_for_context_injected_callables() -> None:
    """Inject custom select_rule and truncate_content for testing/strategies."""
    messages = [
        Message(
            role=Role.USER,
            content="older",
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        ),
        Message(
            role=Role.USER,
            content="newer",
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        ),
    ]

    calls: list[tuple[int, TruncationSpec]] = []

    def fake_select(
        distance: int,
        truncation: TruncationSpec,
    ) -> Truncation | None:
        calls.append((distance, truncation))
        return Truncation(threshold=0, severity=Severity.LIGHT)

    def fake_truncate(content: str, severity: Severity) -> str:
        return f"<{severity.value}:{content}>"

    result = get_truncated_messages_for_context(
        messages,
        select_rule=fake_select,
        truncate_content=fake_truncate,
    )

    assert [distance for distance, _ in calls] == [1, 0]
    assert [m.content for m in result] == ["<1:older>", "<1:newer>"]


def test_get_messages_for_context_defaults_remove_message() -> None:
    """REMOVE severity omits message from result; (-1, LIGHT) keeps as-is."""
    messages = [
        Message(
            role=Role.USER,
            content="remove-me",
            truncation=Truncation(threshold=0, severity=Severity.REMOVE),
        ),
        Message(
            role=Role.USER,
            content="keep-me",
            truncation=Truncation(threshold=-1, severity=Severity.LIGHT),
        ),
    ]

    result = get_truncated_messages_for_context(messages)

    assert len(result) == 1
    assert result[0].content == "keep-me"


# ---------- EventPipe: records messages verbatim (no truncation on save) ----------


@pytest.mark.parametrize(
    "ctx",
    [
        Ctx(threshold=0, severity=Severity.STUB, output_class=Invoke),
        Ctx(threshold=0, severity=Severity.STUB, output_class=LocationStr),
        Ctx(threshold=0, severity=Severity.STUB, output_class=Stop),
        Ctx(threshold=1, severity=Severity.STUB, output_class=Invoke),
        Ctx(threshold=-1, severity=Severity.LIGHT, output_class=Invoke),
    ],
)
def test_pipe_persists_full_content_regardless_of_truncation(
    pipe: EventPipe, tmp_path: Path, ctx: Ctx
) -> None:
    """The pipe records the message verbatim. ``truncation`` governs only the LLM
    context (``get_truncated_messages_for_context``), never the persisted record —
    so a message is saved in full even when its rule would truncate it at
    distance 0."""
    data_path = tmp_path / "data_dir"
    data_path.mkdir()
    sink = PersistenceSink.for_path(data_path)
    pipe.add_sink(sink)
    pipe.initialize(dry_run=False, agent_name="test")
    tool = truncation_test(ctx)
    message = get_finalized_message(tool(input=Empty(), messages=[]), tool)
    pipe(message)
    assert sink.conversations_location is not None
    saved_content = json.loads(sink.conversations_location.read_text(encoding="utf-8"))[
        "messages"
    ][0]["content"]
    marker = _long_marker(ctx.output_class)
    assert marker in saved_content
    assert TRUNCATED_PLACEHOLDER not in saved_content


# ---------- Graded truncation (list) and single Truncation ----------


def test_graded_truncation_list() -> None:
    """Graded rules [(0, LIGHT), (4, STUB), (10, REMOVE)] at distances 0,3,5,11.

    Distance 0-3: LIGHT (truncate strings to ~200 chars).
    Distance 4-9: STUB (stub with placeholder).
    Distance 10+: REMOVE (omit from result).
    """
    graded_rules: list[Truncation] = [
        Truncation(threshold=0, severity=Severity.LIGHT),
        Truncation(threshold=4, severity=Severity.STUB),
        Truncation(threshold=10, severity=Severity.REMOVE),
    ]
    content = json.dumps({"caller": "graded_test", "value": "z" * LIGHT_MAX_CHARS * 2})
    messages: list[Message] = [
        Message(role=Role.USER, content=content, truncation=graded_rules)
        for _ in range(12)
    ]
    result = get_truncated_messages_for_context(messages)
    # Distances 10, 11 -> REMOVE -> 2 messages omitted
    assert len(result) == 10
    # Last 4 (dist 0-3): LIGHT
    for m in result[-4:]:
        data = json.loads(m.content)
        assert "..." in data.get("value", "")
    # Next 6 (dist 4-9): STUB
    for m in result[-10:-4]:
        assert TRUNCATED_PLACEHOLDER in m.content
        assert "z" * 100 not in m.content


def test_single_truncation() -> None:
    """Single Truncation(threshold=1, STUB) works like before."""
    messages: list[Message] = [
        Message(
            role=Role.USER,
            content=json.dumps({"caller": "test", "value": "z" * LIGHT_MAX_CHARS * 2}),
            truncation=Truncation(threshold=1, severity=Severity.STUB),
        )
        for _ in range(3)
    ]
    result = get_truncated_messages_for_context(messages)
    assert len(result) == 3
    # Last (dist 0): full
    assert TRUNCATED_PLACEHOLDER not in result[-1].content
    assert "z" * 100 in result[-1].content
    # First two (dist 1, 2): STUB
    for m in result[:-1]:
        assert TRUNCATED_PLACEHOLDER in m.content


# ---------- Regression: a single fresh (distance 0) oversized message ----------


def _huge_value_message(
    truncation: TruncationSpec, value_len: int = LIGHT_MAX_CHARS * 50
) -> Message:
    """A message whose JSON ``value`` is (by default) far larger than the LIGHT cap."""
    return Message(
        role=Role.USER,
        content=json.dumps(
            {
                "caller": "c",
                "action": "a",
                "value": "z" * value_len,
            }
        ),
        truncation=truncation,
    )


def test_default_threshold_is_zero() -> None:
    """The message-class default must truncate at every distance (threshold=0)."""
    assert DEFAULT.threshold == 0
    assert DEFAULT.severity == Severity.LIGHT


def test_default_caps_fresh_oversized_message() -> None:
    """Regression: the freshest message (distance 0) is capped under DEFAULT.

    A single oversized command output (e.g. ``rg`` over a big tree) used to reach
    the model whole, because the rules in play matched nothing at distance 0
    (the old default ``threshold=-1`` and the CLI override ``threshold=5``). With
    ``DEFAULT`` now ``threshold=0``, LIGHT fires immediately on the newest message.
    """
    msg = _huge_value_message(DEFAULT)
    assert msg.truncation == DEFAULT  # no explicit override -> inherits the default
    [out] = get_truncated_messages_for_context([msg])
    assert len(out.content) <= LIGHT_MAX_CHARS + 100


def test_no_truncation_is_explicit_opt_out() -> None:
    """NO_TRUNCATION (threshold=-1) keeps even the freshest message full."""
    assert NO_TRUNCATION.threshold == -1
    msg = _huge_value_message(NO_TRUNCATION)
    [out] = get_truncated_messages_for_context([msg])
    assert out.content == msg.content


def test_graded_caps_fresh_then_ages_out() -> None:
    """GRADED: LIGHT while fresh, STUB from GRADED_STUB_DISTANCE, REMOVE from GRADED_REMOVE_DISTANCE."""
    # Oldest message sits exactly at the REMOVE distance; only the freshest needs the
    # oversized payload (to prove LIGHT capping) — keep the rest small to stay light.
    n = GRADED_REMOVE_DISTANCE + 1
    msgs = [_huge_value_message(GRADED, value_len=8) for _ in range(n - 1)]
    msgs.append(_huge_value_message(GRADED))
    result = get_truncated_messages_for_context(msgs)
    # only messages at distance >= GRADED_REMOVE_DISTANCE drop out: here just the oldest
    assert len(result) == n - 1
    # distance 0 (freshest): present but LIGHT-capped, not the raw payload
    assert len(result[-1].content) <= LIGHT_MAX_CHARS + 100
    # every message in the band GRADED_STUB_DISTANCE <= distance < GRADED_REMOVE_DISTANCE is STUB'd
    stubbed = [m for m in result if TRUNCATED_PLACEHOLDER in m.content]
    assert len(stubbed) == GRADED_REMOVE_DISTANCE - GRADED_STUB_DISTANCE
