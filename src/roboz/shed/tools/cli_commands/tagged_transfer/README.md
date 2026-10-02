# Tagged cp/mv experiment

This opt-in tool tests an ordered, tagged command contract with two commands.
It uses the existing **resolve → permission guard → execute** tool chain. The
new resolver validates the supported syntax and prepares both the executable
argv and its required filesystem operations. It does not use path extraction.
The module-local guard checks every required permission before requesting any
approvals, then passes the prepared command to the existing executor.

Command specifications declare the command token, allowed tag/pattern pairs,
option values, the option terminator, and conflicts. The shared validator reads
those declarations without knowing tag names or flag spellings. The cp/mv
preparation functions interpret destination options; filesystem helpers receive
explicit source and destination paths.

```python
from pathlib import Path

from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands.tagged_transfer import get_run_tagged_file_command

tools = get_run_tagged_file_command(
    base=Path("/absolute/project"),
    default_verdict=ActionVerdict.deny,
    allow_rules=[
        PermissionRule(pattern="src/*", operations={Operation.READ}),
        PermissionRule(pattern="reports/*", operations={Operation.CREATE, Operation.DELETE}),
    ],
)
# Add these tools to an Agent. Only run_tagged_file_command is model-facing;
# the guard and executor are automatic chained steps.
```

## Agent calls

Copy two regular files into an existing directory (`cp -v -t reports/ src/a.txt src/b.txt`):

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Copy the source files into reports.",
  "value": [
    ["cp", "CMD"],
    ["-v", "FLG"],
    ["-t", "FLG"],
    ["reports/", "PTH"],
    ["src/a.txt", "PTH"],
    ["src/b.txt", "PTH"]
  ]
}
```

This requires READ on each source and CREATE on `reports/a.txt` and
`reports/b.txt`. An existing destination file additionally requires READ and
DELETE. Ask rules apply independently to CREATE, READ, and DELETE, including
overwrite requirements. A policy denial prevents all approval prompts; declined
or unavailable approval prevents execution.
Approval requires `y` or `yes`, ignoring case and surrounding whitespace.

Permission patterns match literal POSIX names: spaces and backslashes are
preserved. For example, a rule for `report` does not authorize `report `.
Relative patterns match only within the configured base; absolute patterns can
authorize paths outside it. Patterns may also be supplied as callables, and
`directory/**` continues to match the directory itself and its descendants.

Rename a regular file (`mv -T reports/a.txt reports/renamed.txt`):

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Rename the copied report.",
  "value": [
    ["mv", "CMD"],
    ["-T", "FLG"],
    ["reports/a.txt", "PTH"],
    ["reports/renamed.txt", "PTH"]
  ]
}
```

This requires DELETE on `reports/a.txt` and CREATE on `reports/renamed.txt`.

## Supported subset

- One command per call, beginning with `cp` or `mv` tagged CMD.
- Flags use FLG; literal paths use PTH. ARG has no allowed use in these commands.
- Flags precede positional operands. This avoids dependence on option
  permutation and settings such as `POSIXLY_CORRECT`.
- `-t` / `--target-directory` consumes a following PTH naming an existing directory.
- `-T` / `--no-target-directory` requires one source and an exact file destination.
- `-v` / `--verbose` and `--` are supported. After `--`, only PTH tokens are accepted.
- Without `-t`, the last path is the destination. Multiple sources need a directory.
- Paths are resolved relative to the configured base or supplied as absolute paths.
  Tagged path values beginning with `-` become absolute paths before execution.

Examples rejected before execution:

| Input fragment or operation | Reason |
| --- | --- |
| `["source", "ARG"]` | A transfer operand must be PTH |
| `["-t", "FLG"], ["reports", "ARG"]` | `-t` requires a PTH value |
| `-t reports -v --no-target-directory` | Conflicting options, even when separated |
| `-v --verbose`, `-vt`, `--target-directory=reports` | Repeated, bundled, or attached option forms |
| `-r`, directory sources, or special files | Regular-file transfers only |
| `*.txt` | Glob paths are unsupported |
| A symlink in a source, destination, or path component | Symlinks are unsupported |
| A source or existing destination file with more than one hard link | Hard-linked files are unsupported for both cp and mv |
| Sources mapping to the same output, or a destination aliasing a source | Conflicting transfers |
| A move across filesystems | Copy-and-delete fallback is outside this prototype |

## Boundaries and review

This is an experiment, not an OS sandbox. Filesystem races and broader command
support remain out of scope. Commands use the existing runner's timeout and
output handling; native execution can partially complete a multi-file transfer
before failing. The tagged input is retained through resolution and guarding.
The shared runner executes the prepared command directly; only legacy command
sequences carry a chaining context.

The existing CLI tools and default capabilities are unchanged. Inspect
`contracts.py`, `specs.py`, `helpers.py`, and `guard.py` to review the contract,
intent resolution, and permissions. Run `uv run pytest tests/shed/test_tagged_file_command.py` for
scripted agent calls that copy and move temporary files through the full chain.
