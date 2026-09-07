from pathlib import Path

from roboshed.models import (
    ActionVerdict,
    GuardFileSingle,
    GuardFilesResult,
    Operation,
)
from roboshed.tools.guard import build_guarded_tool_chain, guard_items
from roboshed.tools.types import ResolvedFileCommand

from roboz import Ctx, Empty, Message, Str, tool


class CustomInput(Empty):
    label: str


class CustomPayload(Empty):
    items: list[str]


def test_new_payload_survives_guard_chain_without_shared_type_registration(
    tmp_path: Path,
):
    @tool
    def resolve(input: CustomInput, messages: list[Message]) -> ResolvedFileCommand:
        return ResolvedFileCommand(
            original_input=input,
            items=[
                GuardFileSingle(
                    operation=Operation.READ,
                    location=tmp_path,
                    value=CustomPayload(items=[input.label]),
                )
            ],
        )

    @tool
    def execute(input: GuardFilesResult, messages: list[Message]) -> Str:
        assert isinstance(input.original_input, CustomInput)
        assert isinstance(input.items[0].value, CustomPayload)
        return Str(value=input.items[0].value.items[0])

    ctx = Ctx(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        takes_precedence=ActionVerdict.deny,
        allow=[],
        deny=[],
        ask=[],
    )
    entry, guard, execute_tool = build_guarded_tool_chain(
        entry=resolve, guard_ctx=ctx, execute=execute
    )
    ready = entry(input=CustomInput(label="extension"), messages=[])
    checked = guard(input=ready, messages=[])
    assert execute_tool(input=checked, messages=[]).value == "extension"
    assert checked.model_dump()["items"][0]["value"]["items"] == ["extension"]


def test_specialized_guard_models_round_trip(tmp_path: Path):
    original = CustomInput(label="typed")
    ctx = Ctx(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        takes_precedence=ActionVerdict.deny,
        allow=[],
        deny=[],
        ask=[],
    )
    result = guard_items(
        items_to_guard=[
            GuardFileSingle[CustomPayload](
                operation=Operation.READ,
                location=tmp_path,
                value=CustomPayload(items=["ok"]),
            )
        ],
        original_input=original,
        ctx=ctx,
    )
    restored = GuardFilesResult[CustomInput, CustomPayload].model_validate_json(
        result.model_dump_json()
    )
    assert restored.original_input.label == "typed"
    assert restored.items[0].value.items == ["ok"]
