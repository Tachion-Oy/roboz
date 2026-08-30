"""Constants for the guarded file-command skill."""

GLOB_CHARS = ("*", "?", "[")

# Permission rule patterns
WILDCARD_PATTERN = "**"

# Subprocess
SUBPROCESS_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_CHARS = 100_000

# Error messages (validation & execution)
ERR_COMMAND_NOT_ALLOWED = "Command '{cmd}' not allowed"
ERR_HINT_USE_HELP = " Invoke with command='help' to see available commands."
ERR_FORBIDDEN_PATTERN = "Forbidden pattern '{tok}' in argv"
ERR_ARGS_NOT_ALLOWED = "Arg '{arg}' not allowed by command spec"
ERR_MISSING_WHITELISTED_SUBCOMMAND = (
    "Missing required whitelisted argv token."
)
ERR_SOURCE_DESTINATION_PATHS = (
    "Expected at least one source path and one destination path."
)
ERR_NO_PATHS = "No paths"
ERR_EXIT_NONZERO = "[error] Command failed (exit {code})\n{out}"
ERR_EXIT_NONZERO_NO_OUTPUT = (
    "[error] Command failed (exit {code}) and produced no output: {command}"
)
SUCCESS_NO_OUTPUT = "[success] Command completed with no output: {command}"
PIPE_STDIN_FROM_PREVIOUS_COMMAND = "[stdin: piped from previous command]"
PIPE_OUTPUT_TO_NEXT_COMMAND = "[stdout: piped to next command]"
ERR_TIMEOUT = "Command timed out after {timeout} seconds"
ERR_COMMAND_NOT_FOUND = "Command '{cmd}' not found"
ERR_OUTPUT_TOO_LARGE = (
    "[error] Command output too large ({actual_chars} chars; max {max_chars}). "
    "Chain stopped with no partial output returned.\n"
    "Retry with bounded commands: `wc -l`, `head`, `tail`, "
    "`rg --count`, `rg --max-count`, narrower paths, or pipe into `head`."
)
