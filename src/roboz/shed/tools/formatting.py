"""Output framing and permission messages shared by guarded tools."""


def _with_guard_message(message: str | None, body: str) -> str:
    """Include permission exchanges in tool reports without changing command bytes."""
    return f"{message}\n{body}" if message else body


def _framed_cli_output(command_line: str, body: str) -> str:
    """Wrap successful or error CLI text in begin/end markers so multi-step runs stay legible."""
    body = body.rstrip("\n")
    return f"--- begin: {command_line} ---\n{body}\n--- end: {command_line} ---"
