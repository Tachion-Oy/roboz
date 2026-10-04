"""Execution limits and result messages for file commands."""

# Subprocess
SUBPROCESS_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_CHARS = 100_000

SUCCESS_NO_OUTPUT = "[success] Command completed with no output: {command}"
PIPE_STDIN_FROM_PREVIOUS_COMMAND = "[stdin: piped from previous command]"
PIPE_OUTPUT_TO_NEXT_COMMAND = "[stdout: piped to next command]"
ERR_TIMEOUT = "Command timed out after {timeout} seconds"
ERR_COMMAND_NOT_FOUND = "Command '{cmd}' not found"
ERR_OUTPUT_TOO_LARGE = (
    "[error] Command output too large ({actual_chars} chars; max {max_chars}). "
    "Chain stopped with no partial output returned.\n"
    "Retry with bounded commands: `wc -l`, `head`, `tail`, "
    "`rg --count`, `rg --max-count`, or narrower paths. Start bounded reads with `head` or `tail`."
)
