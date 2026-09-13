"""A list context retains its declared element type at factory binding."""

from roboz import Message, Str, factory


@factory()
def remember_value(input: Str, messages: list[Message], ctx: list[str]) -> Str:
    ctx.append(input.value)
    return input


numbers: list[int] = [1]
# Expected: reportArgumentType; the context must be list[str].
remember_value(numbers)
