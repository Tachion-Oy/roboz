from roboz import Message, Str, factory


@factory
def add_prefix(input: Str, messages: list[Message], ctx: str) -> Str:
    return Str(value=f"{ctx}{input.value}")


add_prefix({"prefix": "hello"})
