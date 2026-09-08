"""Prompts for the constrained CLI tools skill."""

from typing import Final

from roboshed.identifiers import RUN_FILE_COMMAND_TOOL_NAME

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

1. **Commands** — allowed command names, argument restrictions, input JSON shape, chaining (`pipe` / `and`), and worked examples.
2. **Permissions** — same content as the host's path guard: how relative paths use the working directory, **allowed** and **denied** operation patterns (which paths allow read/create/delete), precedence (allow vs deny when both match), default when no rule matches, overwrite vs CREATE/DELETE, optional **ask** rules, and **tool-specific behaviors** (e.g. `tee`, `cp`, `mv`).

That runtime output is authoritative for **this** session. This skill describes usage patterns; it does not duplicate your live allow/deny lines.

## Choose command by intent

- **Discover directories/files:** `find`, `ls`
- **Locate symbols or text quickly in code:** **`rg`** (preferred); fallback **`grep`** with `-R` and `-n`
- **Know size before reading:** `wc -l`
- **Read the start or end of a file:** `head`, `tail`
- **Print a whole file:** `cat` only when needed (see truncation below)

## Creating new files (and recreating missing ones)

- **Create directories first:** `mkdir` (use `"chain":"and"` with later steps when needed).
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

Typical workflow: line count, then head and tail. **One** assistant message can run all three as independent steps with `"chain":"and"` (still a single JSON object):

{"chain": "and", "file_commands": [{"command": "wc", "argv": ["-l", "src/big_file.py"]}, {"command": "head", "argv": ["-n", "80", "src/big_file.py"]}, {"command": "tail", "argv": ["-n", "80", "src/big_file.py"]}]}

Read from a specific start line onward (one call):

{"chain": "pipe", "file_commands": [{"command": "tail", "argv": ["-n", "+400", "src/big_file.py"]}]}

Rough middle window via pipe inside **one** call:

{"file_commands": [{"command": "head", "argv": ["-n", "520", "src/big_file.py"]}, {"command": "tail", "argv": ["-n", "80"]}], "chain": "pipe"}

## `rg` pattern examples (regex and OR)

Simple OR with alternation:

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "roboz|site-packages", "."]}]}

Case-insensitive OR:

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "-i", "todo|fixme|bug", "src/"]}]}

Regex for common Python definitions:

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "^def\\s+[A-Za-z_][A-Za-z0-9_]*\\(", "src/"]}]}

Ignore behavior reminder: `rg` respects `.gitignore`/`.ignore` by default. If a broad search misses expected files, either target the specific directory/file path or add `-uu` to disable ignore filtering.

Target ignored subtree directly:

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "needle", "runtime-data/conversations/"]}]}

Disable ignore filtering explicitly:

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-uu", "-n", "needle", "."]}]}

## Input format

```
{"chain": "pipe"|"and", "file_commands": [{command, argv, stdin?}, ...]}
```

- **file_commands**: each entry has `command`, `argv` (all subprocess-style tokens in order), and optional `stdin`.
- **Argument parsing**: use supported, fully spelled options; unknown options and missing values return a parse error. Use `--` before dash-prefixed file names, or give an explicit path such as `./-notes`. `find` roots need the explicit path form.
- **Search patterns and values**: use `PATTERN path` or `-e PATTERN path`. Values for options such as `--max-count 20`, `--lines=80`, and `-g '*.py'` are kept as values. `rg --files path` lists files without a pattern.
- **Unsupported inputs**: do not use pattern files, explicit ignore/exclusion files, indirect file lists, reference-file options, or subprocess preprocessors. Supply search patterns directly with `-e`, explicit file operands, and filter globs with `-g`/`--include`/`--exclude` as applicable.
- **Copy/move destinations**: use separate literal tokens for `-t DIR` or `--target-directory DIR`.
- **chain**: required on every call. `"pipe"` chains stdout→stdin; `"and"` runs sequentially.
- **Normal shell semantics**: this is the same behavior as `|` and `&&` in a regular shell; only the JSON shape is different.
- **When to use which**: use `"pipe"` only when the next command consumes stdin (`cat|grep`, `grep|wc`). For independent commands (`find` then `find`, `ls` then `find`), use `"and"`.
- **No shell syntax** (`|`, `;`, `&&`) in command strings—use `chain` and multiple `file_commands` entries only (same *behavior* as `|` / `&&`, but never embed those characters in `command` or `argv` tokens).

## How to use `find` correctly

`find` is special about argument order:

- Search roots must come first.
- Predicates/flags come after roots.
- In this tool, keep roots and predicates in one ordered `argv` list (roots first).

Good (two separate `find` roots—**one** call, `"chain":"and"`):

{"chain": "and", "file_commands": [{"command": "find", "argv": [".", "-type", "d", "-name", "roboz"]}, {"command": "find", "argv": [".", "-type", "d", "-name", "site-packages"]}]}

## Examples

Each bullet below shows **one** JSON payload shape for **one** `<<RUN_FILE_COMMAND_TOOL>>` invocation (still wrapped in the agent's outer `action` / `rationale` fields as usual)—never concatenate multiple examples into one assistant reply.

- **help** — Print the full reference for this tool chain (commands, input shape, and path permissions).

{"chain": "pipe", "file_commands": [{"command": "help", "argv": []}]}

- **rg** — Fast search under a path (line numbers via `-n`).

{"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "def main", "src/"]}]}

- **cat** — Print the contents of a file.

{"chain": "pipe", "file_commands": [{"command": "cat", "argv": ["src/main.py"]}]}

- **cat | grep** — Read a file and search its lines for a pattern (pipe: first command's output feeds the second).

{"file_commands": [{"command": "cat", "argv": ["file.txt"]}, {"command": "grep", "argv": ["pattern"]}], "chain": "pipe"}

- **mkdir && touch** — Create a directory, then create a file inside it (and: run two steps in order).

{"file_commands": [{"command": "mkdir", "argv": ["sub"]}, {"command": "touch", "argv": ["sub/file"]}], "chain": "and"}

- **tee (create with content)** — Create or overwrite a file with explicit stdin content.

{"chain": "pipe", "file_commands": [{"command": "tee", "argv": ["sub/file.txt"], "stdin": "line 1\\nline 2\\n"}]}

- **find then find** — Two independent discovery commands should use `and`, not `pipe`.

{"file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}, {"command": "find", "argv": [".", "-type", "d"]}], "chain": "and"}

- **grep** — Recursive tree search under a directory (GNU `grep` needs `-R` when the path is a folder); line numbers via `-n`.

{"chain": "pipe", "file_commands": [{"command": "grep", "argv": ["-n", "-R", "TODO", "src/"]}]}

- **find** — One `argv`: search roots first, then predicates (same subprocess order as CLI `find`).

{"chain": "pipe", "file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}]}

- **Git** — Not available in this file CLI.

- **diff** — Compare two files and show differences.

{"chain": "pipe", "file_commands": [{"command": "diff", "argv": ["old.py", "new.py"]}]}

- **cp** — Copy a file.

{"chain": "pipe", "file_commands": [{"command": "cp", "argv": ["source.txt", "copy.txt"]}]}

- **cp -r** — Copy a directory tree.

{"chain": "pipe", "file_commands": [{"command": "cp", "argv": ["-r", "source_dir", "copy_dir"]}]}

- **mv** — Rename or move a file or directory.

{"chain": "pipe", "file_commands": [{"command": "mv", "argv": ["old_name.txt", "new_name.txt"]}]}

Note: which commands are permitted and how paths map to operations follow **`help`** and the tool specifications for this deployment.

Path guards authorize explicit operands or an implicit working directory. They do not individually authorize descendants visited by a directory command or implicit ignore/configuration files. These tools are not a process sandbox.
"""

INSTRUCTIONS: Final[str] = _INSTRUCTIONS_TEMPLATE.replace(
    _RUN_FILE_COMMAND_PH, RUN_FILE_COMMAND_TOOL_NAME
)
