"""Shared test doubles for user interaction, injected via the real binding seam."""

import pytest

from roboz.runtime import Output
from roboz.runtime.io import (
    bind_api_user_io,
    bind_output,
    reset_api_user_io,
    reset_output,
)


class FakeUserIO:
    """A ``UserIO`` implementation for tests.

    Scripted replies are returned in order; a ``None`` reply simulates a timeout
    (the host did not get a reply in time). Records prompts and forwarded timeouts.
    """

    def __init__(self, replies: list[str | None]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []
        self.timeouts: list[float | None] = []

    def request_input(self, message: str, timeout: float | None = None) -> str | None:
        self.prompts.append(message)
        self.timeouts.append(timeout)
        return self._replies.pop(0)

    def notify(self, message: str) -> None:
        self.prompts.append(f"[notify]{message}")


@pytest.fixture
def bind_user_io():
    """Factory: bind a ``FakeUserIO`` for ``Output.API``; reset on test teardown."""
    bound: list = []

    def _bind(replies: list[str | None]) -> FakeUserIO:
        io = FakeUserIO(replies)
        bound.append((bind_output(Output.API), bind_api_user_io(io)))
        return io

    yield _bind

    for output_token, io_token in reversed(bound):
        reset_api_user_io(io_token)
        reset_output(output_token)
