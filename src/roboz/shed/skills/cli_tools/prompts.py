"""Prompts for the constrained CLI tools skill."""

from typing import Final

from roboz.shed.identifiers import RUN_FILE_COMMAND_TOOL_NAME

DESCRIPTION: Final[str] = (
    f"How to use Roboz's constrained Unix file CLI via `{RUN_FILE_COMMAND_TOOL_NAME}`: input shape, "
    "chaining, commands, oversized-output fail-closed behavior, and how to read this run's "
    "path permissions (via `help`)."
)

_RUN_FILE_COMMAND_PH = "<<RUN_FILE_COMMAND_TOOL>>"

_INSTRUCTIONS_TEMPLATE = """## Constrained file CLI (`<<RUN_FILE_COMMAND_TOOL>>`)

Use the **`<<RUN_FILE_COMMAND_TOOL>>`** tool with the configured working directory as **shorthand** for relative paths; permissions come only from allow/deny/ask rules (see `help`).

### Full reference: `help`

Invoke **`command: help`** with empty `argv`. The tool returns **one combined document**:

1. **Commands** — allowed command names, forbidden argument substrings, input JSON shape, chaining (`|` / `&&` / `||` / `;`), and worked examples.
2. **Permissions** — same content as the host's path guard: how relative paths use the working directory, **allowed** and **denied** operation patterns (which paths allow read/create/delete), precedence (allow vs deny when both match), default when no rule matches, overwrite vs CREATE/DELETE, optional **ask** rules, and **tool-specific behaviors** (e.g. `tee`, `cp`, `mv`).

That runtime output is authoritative for **this** session. This skill describes usage patterns; it does not duplicate your live allow/deny lines.

## Choose command by intent

- **Discover directories/files:** `find`, `ls`
- **Locate symbols or text quickly in code:** **`rg`** (preferred); fallback **`grep`** with `-R` and `-n`
- **Know size before reading:** `wc -l`
- **Read the start or end of a file:** `head`, `tail`
- **Print a whole file:** `cat` only when needed (see truncation below)

## Creating new files (and recreating missing ones)

- **Create directories first:** `mkdir` (use `"chain":"&&"` with dependent steps).
- **Create empty files:** `touch`.
- **Create/populate file content:** `tee` with `stdin` content.

## Moving or renaming files and directories

- Use `mv` for rename/move operations (`mv SOURCE DEST` or `mv -t DEST_DIR SOURCE...`).
- `mv` does not need a recursive flag for directories; moving a directory path moves the tree.
- `mv` authorization checks are split: source paths require DELETE; destination paths require CREATE.
- Overwriting an existing destination also requires READ and DELETE permission there.

## Copying files and directories

- Use `cp SOURCE DEST` for files and `cp -r SOURCE_DIR DEST` for directories.
- `cp` authorization checks are split: source paths require READ; destination paths require CREATE.

## Chain operators

Supply one required `chain` value for every call, including a single command. `"|"` forwards only stdout as the next command's stdin and runs every stage, even after a nonzero exit. `"&&"` runs the next command only after exit status zero. `"||"` runs the next command only after failure. `";"` always runs the next command after an ordinary execution failure. The same operator applies to every step. Only `"|"` replaces explicit `stdin` on later commands.

Success means exit status zero, even with empty output. Search and `diff` results follow exit status. The overall result reports the last command executed; for `"|"`, this is the final stage. A missing permitted executable has status 127. Timeouts count as failures for `"&&"`, `"||"`, and `";"`; a pipeline timeout stops immediately. Validation, help, permission denial, unexpected errors, and oversized output stop every chain. Earlier file changes are not rolled back.

Pipelines buffer each command's complete stdout before starting the next command. They do not reproduce streaming, backpressure, concurrent scheduling, or SIGPIPE behavior. Pass argv tokens directly; mixed operators, grouping, redirection, and shell parsing are unavailable.

## Large output behavior (fail closed)

If a command emits too much text, `<<RUN_FILE_COMMAND_TOOL>>` returns an explicit **error-style** response and stops the chain. It does **not** return a partial/truncated command result that could be mistaken for complete output (no partial output returned).

This protects result semantics. A silently cut `rg`/`grep`/`cat` output can make it look like a match or line does not exist when it actually does.

**What to do instead:** Prefer **narrow, targeted** inspection so each result stays bounded:

- **`rg`** or **`grep`** (with `-n`; add `-R` for `grep` when searching trees) to find symbols, classes, or TODOs.
- **Bounded search first:** `rg --count PATTERN path` or `rg --max-count 20 PATTERN path`.
- **`head`** and **`tail`** to read the start or end of a file explicitly.
- **`wc`** for line counts when you need to know size before reading.
- **Multiple small commands** beat one huge `cat`: one pass for imports, another for a class, then `tail` for the bottom.

If you must understand a long file end-to-end, **plan several targeted commands**—either in **one** `<<RUN_FILE_COMMAND_TOOL>>` call multiple `file_commands` entries, or across **separate** assistant turns (each turn: **one** JSON object only). Never emit multiple JSON objects in a single assistant message.

### Large file read patterns (safe and bounded)

Typical workflow: line count, then head and tail. **One** assistant message can run all three as independent steps with `"chain":";"` (still a single JSON object):

{"chain": ";", "file_commands": [{"command": "wc", "argv": ["-l", "src/big_file.py"]}, {"command": "head", "argv": ["-n", "80", "src/big_file.py"]}, {"command": "tail", "argv": ["-n", "80", "src/big_file.py"]}]}

Read from a specific start line onward (one call):

{"chain": "|", "file_commands": [{"command": "tail", "argv": ["-n", "+400", "src/big_file.py"]}]}

Rough middle window via pipe inside **one** call:

{"file_commands": [{"command": "head", "argv": ["-n", "520", "src/big_file.py"]}, {"command": "tail", "argv": ["-n", "80"]}], "chain": "|"}

## `rg` pattern examples (regex and OR)

Simple OR with alternation:

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-n", "roboz|site-packages", "."]}]}

Case-insensitive OR:

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-n", "-i", "todo|fixme|bug", "src/"]}]}

Regex for common Python definitions:

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-n", "^def\\\\s+[A-Za-z_][A-Za-z0-9_]*\\\\(", "src/"]}]}

Ignore behavior reminder: `rg` respects `.gitignore`/`.ignore` by default. If a broad search misses expected files, either target the specific directory/file path or add `-uu` to disable ignore filtering.

Target ignored subtree directly:

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-n", "needle", "runtime-data/conversations/"]}]}

Disable ignore filtering explicitly:

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-uu", "-n", "needle", "."]}]}

## Input format

```
{"chain": "|"|"&&"|"||"|";", "file_commands": [{command, argv, stdin?}, ...]}
```

- **file_commands**: each entry has `command`, `argv` (all subprocess-style tokens in order), and optional `stdin`.
- **chain**: required on every call; choose `"|"`, `"&&"`, `"||"`, or `";"` using the rules above.
- **No shell operators in command strings**: use `chain` and multiple `file_commands` entries. Regex alternation inside an argument is allowed.

## How to use `find` correctly

`find` is special about argument order:

- Search roots must come first.
- Predicates/flags come after roots.
- In this tool, keep roots and predicates in one ordered `argv` list (roots first).

Good (two separate `find` roots—**one** call, `"chain":";"`):

{"chain": ";", "file_commands": [{"command": "find", "argv": [".", "-type", "d", "-name", "roboz"]}, {"command": "find", "argv": [".", "-type", "d", "-name", "site-packages"]}]}

## Examples

Each bullet below shows **one** JSON payload shape for **one** `<<RUN_FILE_COMMAND_TOOL>>` invocation (still wrapped in the agent's outer `action` / `rationale` fields as usual)—never concatenate multiple examples into one assistant reply.

- **help** — Print the full reference for this tool chain (commands, input shape, and path permissions).

{"chain": "|", "file_commands": [{"command": "help", "argv": []}]}

- **rg** — Fast search under a path (line numbers via `-n`).

{"chain": "|", "file_commands": [{"command": "rg", "argv": ["-n", "def main", "src/"]}]}

- **cat** — Print the contents of a file.

{"chain": "|", "file_commands": [{"command": "cat", "argv": ["src/main.py"]}]}

- **cat | grep** — Read a file and search its lines for a pattern (pipe: first command's output feeds the second).

{"file_commands": [{"command": "cat", "argv": ["file.txt"]}, {"command": "grep", "argv": ["pattern"]}], "chain": "|"}

- **mkdir && touch** — Create a directory, then create a file inside it after success.

{"file_commands": [{"command": "mkdir", "argv": ["sub"]}, {"command": "touch", "argv": ["sub/file"]}], "chain": "&&"}

- **cat primary || cat backup** — Read the backup only if reading the primary file fails.

{"chain": "||", "file_commands": [{"command": "cat", "argv": ["primary.txt"]}, {"command": "cat", "argv": ["backup.txt"]}]}

- **tee (create with content)** — Create or overwrite a file with explicit stdin content.

{"chain": "|", "file_commands": [{"command": "tee", "argv": ["sub/file.txt"], "stdin": "line 1\\nline 2\\n"}]}

- **find then find** — Run both independent discoveries even if the first fails.

{"file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}, {"command": "find", "argv": [".", "-type", "d"]}], "chain": ";"}

- **grep** — Recursive tree search under a directory (GNU `grep` needs `-R` when the path is a folder); line numbers via `-n`.

{"chain": "|", "file_commands": [{"command": "grep", "argv": ["-n", "-R", "TODO", "src/"]}]}

- **find** — One `argv`: search roots first, then predicates (same subprocess order as CLI `find`).

{"chain": "|", "file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}]}

- **Git** — Not available in this file CLI.

- **diff** — Compare two files and show differences.

{"chain": "|", "file_commands": [{"command": "diff", "argv": ["old.py", "new.py"]}]}

- **cp** — Copy a file.

{"chain": "|", "file_commands": [{"command": "cp", "argv": ["source.txt", "copy.txt"]}]}

- **cp -r** — Copy a directory tree.

{"chain": "|", "file_commands": [{"command": "cp", "argv": ["-r", "source_dir", "copy_dir"]}]}

- **mv** — Rename or move a file or directory.

{"chain": "|", "file_commands": [{"command": "mv", "argv": ["old_name.txt", "new_name.txt"]}]}

Note: which commands are permitted and how paths map to operations follow **`help`** and the tool specifications for this deployment.
"""

INSTRUCTIONS: Final[str] = _INSTRUCTIONS_TEMPLATE.replace(
    _RUN_FILE_COMMAND_PH, RUN_FILE_COMMAND_TOOL_NAME
)
