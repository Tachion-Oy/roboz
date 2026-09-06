from roboz import Ctx, Message, Str, factory


@factory
def add_prefix(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return Str(value=f"{ctx.prefix}{input.value}")


add_prefix({"prefix": "hello"})
