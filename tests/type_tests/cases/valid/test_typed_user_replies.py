"""User interaction carries typed text or explicit image results."""

from typing import assert_type

from roboz.models import Image, Str, StrImage
from roboz.runtime import UserIO, interact_with_user


class Replies:
    def __init__(self, reply: Str | StrImage | None) -> None:
        self.reply = reply

    def request_input(
        self, message: str, timeout: float | None = None, *, accepts_images: bool = False
    ) -> Str | StrImage | None:
        return self.reply

    def notify(self, message: str) -> None:
        pass


def inspect_reply(io: UserIO) -> None:
    assert_type(io.request_input("Question?", accepts_images=True), Str | StrImage | None)
    assert_type(interact_with_user("Question?", True, accepts_images=True), Str | StrImage | None)


image = Image(url="https://example.com/chart.png")
inspect_reply(Replies(Str(value="Text")))
inspect_reply(Replies(StrImage(value="", images=[image])))
inspect_reply(Replies(None))
