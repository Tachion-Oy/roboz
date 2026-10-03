# Tagged cp/mv experiment

This opt-in tool tests an ordered, tagged command contract with two commands.
It uses the existing **resolve → permission guard → execute** tool chain. The
new resolver validates the supported syntax and prepares both the executable
argv and its required filesystem operations. It does not use path extraction.
The module-local guard checks every required permission before requesting any
approvals, then passes the prepared command to the tagged executor. One passive
continuation resolver loops each reached command back through the same guard and
executor; the four tool instances are fixed for sequences of any length.

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

### Command sequences

Tag control operators `CTL` in the same flat list:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Rename the report and back it up, with a fallback source.",
  "value": [
    ["mv", "CMD"], ["hello.txt", "PTH"], ["renamed.txt", "PTH"],
    ["&&", "CTL"],
    ["cp", "CMD"], ["renamed.txt", "PTH"], ["backup.txt", "PTH"],
    ["||", "CTL"],
    ["cp", "CMD"], ["fallback.txt", "PTH"], ["backup.txt", "PTH"]
  ]
}
```

`&&` continues on success, `||` on failure, `;` unconditionally, and `|` passes
stdout to the next command. Pipelines bind first; `&&` and `||` have equal
precedence and associate left to right, as in Bash. Thus `a || b && c` runs `c`
after either `a` or `b` succeeds. Skipping a pipeline skips all its stages and
retains the last executed status. The final result reports that status.

Only CTL tags split commands: `["&&", "PTH"]` names a literal file. There is no
separate chain field. The input types and schema restrict CMD values to `cp`/`mv`
and CTL values to the four supported operators. Every segment starts with CMD;
leading, trailing, adjacent, or unsupported control operators reject the entire
sequence before execution.
Individual command options and paths are checked only when reached. Earlier
commands can create files used by later source patterns. Skipped commands do
not expand paths, look up executables, or request approvals.

Every reached command repeats resolution, guarding, and execution. Preparation
errors, policy denials, and declined approvals produce status 1 without launching
a process; `||` or `;` can continue to another independently guarded command.
Process exit statuses are preserved; missing executables return 127, launch
permission failures 126, signals 128 plus the signal number, and timeouts 124.
Timeouts discard partial output. Cancellation, oversized output, and unexpected
execution errors abort the sequence.

Pipes are buffered and sequential so every stage is guarded before execution.
They preserve stdout exactly, including empty output, and keep stderr separate.
The last pipeline stage determines its status (no `pipefail`). The current
`cp`/`mv` commands do not consume stdin. There is no rollback of earlier writes.

### Source patterns

Use single stars in any source-path component:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Copy Python sources into backup.",
  "value": [
    ["cp", "CMD"],
    ["projects/*/src/*.py", "PTH"],
    ["backup", "PTH"]
  ]
}
```

`*` matches zero or more characters within one component. Patterns such as
`src/*`, `src/*.py`, and `report-*-final.*` are supported. Hidden names require
an explicitly leading dot in each component: `src/*` excludes them and `src/.*`
includes them. Wildcards never select the special `.` or `..` directory entries.
These follow the [Bash filename-matching rules](https://www.gnu.org/s/bash/manual/html_node/Filename-Expansion.html).

Source operands retain their order; each pattern's matches are sorted in
C-locale filesystem byte order. If any pattern has no matches, that command
fails before execution. Missing or non-directory branches contribute no
matches; symlinks, permission errors, and other traversal errors reject the
command. Matched filenames are literal executable arguments, including spaces,
leading dashes, and wildcard characters; they are never expanded again.

Destinations, both the last positional PTH and `-t` values, remain literal and
reject `*`. `**`, `?`, and bracket patterns are unsupported in all input paths.
There is no shell quoting, variable expansion, or command substitution.
Multiple-source and `-T` constraints apply to the expanded source count.
Duplicate sources, overlaps, colliding outputs, and unsupported matched entries
reject the entire command. Every match receives the existing permission checks.

A trailing slash selects directories. Suffixes such as `/.` and `/..` retain
their native meaning. For a single match, `cp -R project/s*/. backup` copies the
directory contents directly into `backup`. Conflicting transfer roots are still
rejected.
Matched directories require a recursive flag for `cp`; `mv` needs none.

### Directories

Copy a tree (`cp -R src backup`):

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Copy the source tree into backup.",
  "value": [
    ["cp", "CMD"],
    ["-R", "FLG"],
    ["src", "PTH"],
    ["backup", "PTH"]
  ]
}
```

The native GNU commands perform the transfers. Their directory behavior is:

| Call | Destination behavior |
| --- | --- |
| `cp -R src new` | Create `new` with the source contents |
| `cp -R src backup` (existing directory) | Copy into `backup/src`, merging if it exists |
| `cp -R -T src backup` | Copy directly into `backup`, merging if it exists |
| `cp -R src/. backup` | Copy contents directly into `backup` |
| `mv src new` | Rename the directory, without a recursive flag |
| `mv src backup` (existing directory) | Move to `backup/src`; replace an empty directory there, reject a populated one |

Every source entry, including hidden files and empty directories, requires READ
for a copy or DELETE for a move. Every mapped destination requires CREATE.
Overwriting a file, or replacing an empty directory with `mv`, additionally
requires READ and DELETE at that destination. A copy merge requires CREATE on
existing directories without treating them as deleted. Unrelated destination
contents are preserved and do not require permissions.

All descendant checks and approvals complete before execution. Denying one
entry prevents the entire command. `-f` follows native force behavior and does
not bypass any policy or approval. Traversal errors and unsupported entries
reject the command before execution.

Executable paths retain meaningful spelling such as `src/.` and trailing
slashes; canonical paths are used separately for permission checks. A directory
can be transferred to a missing `new/`, while `new/.` still requires `new` to
exist. See the [GNU cp manual](https://www.gnu.org/s/coreutils/manual/html_node/cp-invocation.html)
and [GNU mv manual](https://www.gnu.org/s/coreutils/manual/html_node/mv-invocation.html).

## Supported subset

- One or more commands, each beginning with `cp` or `mv` tagged CMD, separated
  by `&&`, `||`, `;`, or `|` tagged CTL.
- Flags use FLG; source patterns and literal destinations use PTH. ARG has no
  allowed use in these commands.
- Flags precede positional operands. This avoids dependence on option
  permutation and settings such as `POSIXLY_CORRECT`.
- `-t` / `--target-directory` consumes a following PTH naming an existing directory.
- `-T` / `--no-target-directory` requires one expanded source and an exact destination.
- `cp` accepts `-r` / `-R` / `--recursive` for directory copies. `mv` moves
  directories without a recursive flag.
- Both commands accept `-f` / `--force` and `--strip-trailing-slashes`.
  No-clobber (`-n` / `--no-clobber`) is not supported in this iteration.
- `-v` / `--verbose` and `--` are supported. After `--`, only PTH tokens are accepted.
- Without `-t`, the last path is the destination. Multiple sources need a directory.
- Paths are interpreted relative to the configured base or supplied as absolute paths.
  Tagged path values beginning with `-` become absolute paths before execution.

Examples rejected before execution:

| Input fragment or operation | Reason |
| --- | --- |
| `["source", "ARG"]` | A transfer operand must be PTH |
| `["-t", "FLG"], ["reports", "ARG"]` | `-t` requires a PTH value |
| `-t reports -v --no-target-directory` | Conflicting options, even when separated |
| `-v --verbose`, `-vt`, `--target-directory=reports` | Repeated, bundled, or attached option forms |
| Directory copies without a recursive flag, or `mv -r` | Native commands require recursion only for directory copies |
| A special file anywhere in the source tree or at a mapped destination | Only regular files and directories are supported |
| `**`, `?.txt`, `[ab].txt`, or a destination containing `*` | Unsupported input patterns or a nonliteral destination |
| A source pattern with no matches | All operands must resolve before execution |
| A symlink in a source, destination, or path component | Symlinks are unsupported |
| A source or existing destination file with more than one hard link | Hard-linked files are unsupported for both cp and mv |
| Overlapping sources or outputs, a source/destination overlap, or a destination aliasing a source | Conflicting transfers |
| Moving a directory onto a populated effective destination directory | Native `mv` does not merge directories |
| A move across filesystems | Copy-and-delete fallback is outside this prototype |

## Boundaries and review

This is an experiment, not an OS sandbox. Filesystem races and broader command
support remain out of scope. Commands use the existing runner's timeout and
output handling; native execution can partially complete a multi-file transfer
before failing. The tagged input is retained through resolution and guarding.
The shared process runner executes the prepared argv directly. The public input
contains only the tagged value; its validator checks the command/CTL grammar.
The executor consumes the remaining tokens directly, using the latest exit
status to skip or select the next command.
The guard transports the execution payload through its existing original-input
field: remaining tokens, pipe input, accumulated output, and preparation errors.
The original request is retained in full. Both entry and continuation use the
same step preparation; there is no stored list of parsed commands or operators.

The existing CLI tools and default capabilities are unchanged. Inspect
`contracts.py`, `sequence.py`, `command.py`, `guard.py`, and `execute.py` to review
the contract and guarded loop. Run `uv run pytest tests/shed/test_tagged*.py` for
scripted agent calls that copy and move temporary files through the full chain.
