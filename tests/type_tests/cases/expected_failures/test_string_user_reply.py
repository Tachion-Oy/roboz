"""User-input adapters must return typed replies rather than strings."""

from roboz.runtime import UserIO


class StringReplies:
    def request_input(self, message: str, timeout: float | None = None) -> str | None:
        return "answer"

    def notify(self, message: str) -> None:
        pass


io: UserIO = StringReplies()
