"""Output framing shared by file commands and patching."""

def _framed_cli_output(command_line: str, body: str) -> str:
    """Wrap successful or error CLI text in begin/end markers so multi-step runs stay legible."""
    body = body.rstrip("\n")
    return f"--- begin: {command_line} ---\n{body}\n--- end: {command_line} ---"
