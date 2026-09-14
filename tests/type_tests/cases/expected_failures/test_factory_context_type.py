from roboz.models import Message, Str
from roboz import factory


@factory
def add_prefix(input: Str, messages: list[Message], ctx: str) -> Str:
    return Str(value=f"{ctx}{input.value}")


add_prefix({"prefix": "hello"})
