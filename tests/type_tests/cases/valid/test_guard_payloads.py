from pathlib import Path
from typing import assert_type

from roboz.shed.models import ApplyPatch, GuardFileSingle, GuardFilesResult, Operation
from roboz.shed.tools.guard import guard_items

from roboz.shed.tools import GuardContext
from roboz.models import Str


def typed_guard(ctx: GuardContext) -> None:
    items = [
        GuardFileSingle[Str](
            operation=Operation.READ, location=Path("/tmp"), value=Str(value="payload")
        )
    ]
    file_result = guard_items(
        items_to_guard=items,
        original_input=ApplyPatch(path="a", old_string="", new_string="b"),
        ctx=ctx,
    )
    assert_type(file_result, GuardFilesResult[ApplyPatch, Str])
