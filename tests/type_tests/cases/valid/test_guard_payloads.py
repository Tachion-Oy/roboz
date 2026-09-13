from pathlib import Path
from typing import assert_type

from roboshed.models import ApplyPatch, GuardFileSingle, GuardFilesResult, Operation
from roboshed.tools.email.inputs import CreateEmailDraft
from roboshed.tools.guard import guard_items

from roboshed.tools import GuardContext
from roboz import Str


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
    email_result = guard_items(
        items_to_guard=items,
        original_input=CreateEmailDraft(
            to=["a@example.com"], subject="Hi", body_text="Hi"
        ),
        ctx=ctx,
    )
    assert_type(file_result, GuardFilesResult[ApplyPatch, Str])
    assert_type(email_result, GuardFilesResult[CreateEmailDraft, Str])
