# Tagged CLI v2

This opt-in tool supports `cp`, `mv`, `pwd`, `cat`, `head`, `tail`, `wc`, `tee`,
`touch`, `mkdir`, `grep`, `rg`, `ls`, and `find` through an ordered, tagged command contract.
The existing v1 CLI and default capabilities remain available. Public imports
from `cli_commands.tagged_transfer` are compatibility re-exports of this package.
It uses the existing **resolve → permission guard → execute** tool chain. The
new resolver validates the supported syntax and prepares both the executable
argv and its required filesystem operations. It does not use path extraction.
The module-local guard checks every required permission before requesting
approvals, then passes the prepared command to the tagged
executor. One passive continuation resolver loops each reached command back
through the same guard and executor; the four tool instances are fixed for
sequences of any length.

Command specifications declare the command token, allowed tag/pattern pairs,
option values, the option terminator, and conflicts. The shared validator reads
those declarations without knowing tag names or flag spellings. Discovery
commands validate their native argument ordering within their own modules. The cp/mv
preparation functions interpret destination options; filesystem helpers receive
explicit source and destination paths.

Each command has its own module under `commands/`. Transfers share mapping and
collision checks; readers share regular-file preparation. `tokens.py` validates
tags and options, `paths.py` handles path resolution and patterns, and `specs.py`
registers the supported commands.

```python
from pathlib import Path

from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands_v2 import get_run_tagged_file_command

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

### Read commands

Readers support the following flags as separate FLG tokens, before operands:

| Command | Supported options |
| --- | --- |
| `pwd` | `-P` / `--physical`; physical output is always used; no operands |
| `cat` | `-n` / `--number`, `-b` / `--number-nonblank`, `-s` / `--squeeze-blank`, `-E` / `--show-ends`, `-T` / `--show-tabs` |
| `head`, `tail` | `-n` / `--lines` or `-c` / `--bytes`, followed by an unsigned decimal ARG; `-q` / `--quiet` / `--silent` or `-v` / `--verbose` |
| `wc` | Combinations of `-l` / `--lines`, `-w` / `--words`, `-c` / `--bytes`, `-m` / `--chars`, `-L` / `--max-line-length` |

Defaults follow the native GNU commands: head/tail select ten lines and wc
reports its default counts. Counts may be zero, but signed values and size
suffixes are unsupported. Line/byte and quiet/verbose modes conflict. For cat,
`-b` overrides `-n`. Unknown, bundled, attached, and repeated options are
rejected, including aliases of the same option. `--` ends options.
Follow mode and indirect file lists such as `wc --files0-from` are unsupported.

File operands use PTH and support the source patterns described below. Every
selected path must be a regular file without symlinks or hard links; selected
directories and special files reject the whole command. READ is required on
every selected file. Operands keep their order and repeats, while permission
checks and approvals are deduplicated per file. `pwd` requires READ on the
configured base itself.

Omit file operands or use `["-", "ARG"]` to read stdin. `["-", "PTH"]`
reads the literal file named `-`. File and stdin operands may be interleaved.
Without an incoming pipe, stdin is empty; the tool never waits for terminal
input. Stdin-only readers need no filesystem permissions.

For example, count the first two lines of a file:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Count the first two lines.",
  "value": [
    ["cat", "CMD"], ["data.txt", "PTH"], ["|", "CTL"],
    ["head", "CMD"], ["-n", "FLG"], ["2", "ARG"], ["|", "CTL"],
    ["wc", "CMD"], ["-l", "FLG"]
  ]
}
```

### Search file contents

`grep` and `rg` take one pattern ARG, followed by file/directory PTH operands or
stdin `["-", "ARG"]`. A pattern is passed literally to the native regex engine;
shell characters and a leading dash have no shell or option meaning. File PTHs
support the existing source patterns and preserve literal dash filenames.
`rg` requires the ripgrep executable to be installed separately and available
on `PATH`; a missing executable returns command-not-found status `127`.
Without input operands they read stdin, except recursive `grep` searches the
configured base. Stdin is empty without an incoming pipe and never waits for a
terminal. Explicit stdin may be mixed with file operands.

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Find outstanding work in the source tree.",
  "value": [
    ["rg", "CMD"], ["-n", "FLG"], ["-i", "FLG"],
    ["todo|fixme", "ARG"], ["src", "PTH"]
  ]
}
```

| Command | Supported options (separate FLG tokens before the pattern) |
| --- | --- |
| Both | `-n` / `--line-number`, `-i` / `--ignore-case`, `-v` / `--invert-match`, `-F` / `--fixed-strings`, `-w` / `--word-regexp`, `-x` / `--line-regexp`, `-c` / `--count`, `-l` / `--files-with-matches`, `-q` / `--quiet`, `-o` / `--only-matching`, `-H` / `--with-filename`, `--` |
| Both, with unsigned decimal ARG values | `-m` / `--max-count`, `-A` / `--after-context`, `-B` / `--before-context`, `-C` / `--context`; zero is allowed |
| `grep` | `-E` / `--extended-regexp`, `-h` / `--no-filename`, `-r` / `--recursive`, `-R` / `--dereference-recursive` |
| `rg` | `-I` / `--no-filename`, `--hidden`, `--no-ignore`, `-uu` |

GNU grep uses basic regexes by default; ripgrep uses its native regex syntax.
`-F` selects fixed strings. Grep's `-E` and `-F` conflict, as do filename display
and suppression options. Unknown, repeated, bundled, and attached options are
rejected. The single allowlisted token `-uu` is an exception: it includes hidden
files and disables ignores. Pattern files, custom ignore files, preprocessors,
compressed searches, and symlink-following options for rg are unsupported.

Grep requires a recursive flag for directory inputs; rg recurses by default.
Ripgrep honors local and ancestor `.gitignore`, `.ignore`, and `.rgignore`
files with its native precedence and Git-repository detection. Explicit file
operands, including files selected by PTH globs, override ignore filtering.
Ambient ripgrep configuration, global Git ignores, and Git `info/exclude` files
are disabled so they cannot introduce undeclared reads or commands.

Regular search inputs require READ and may not be hard-linked or special files.
Explicit symlinks are rejected. Native grep `-r` and rg skip descendant symlinks
and special files; grep `-R` rejects trees containing them before execution.

Recursive searches require READ on every directory and regular file in the
candidate tree, including hidden and ignored files. Ripgrep also requires READ
on potential local/ancestor ignore files, unless `--no-ignore` or `-uu` disables
ignore processing. **A denied ignored file blocks the search**, even though
native ripgrep would skip it. The same file restrictions apply throughout the
candidate tree, including hard-link rejection.

Preparation only inspects filesystem entries; it does not read ignore-file or
search-file contents. The existing guard checks all required permissions before
requesting approvals, deduplicated across operands. After authorization, one
native invocation applies ignore rules and searches. The shared preparation and
execution contracts are unchanged. Recursive preparation has a separate 60-second
deadline covering tree traversal and ignore-file checks. Expiry fails preparation
before approval or execution, with status `1`. The deadline is checked between
filesystem operations; it cannot interrupt a blocked filesystem call. Memory use
remains proportional to the candidate tree. Native execution then has its own
60-second subprocess timeout.

Native stdout, stderr, and status determine command chaining. Status `0` means
a match, `1` means no matches, and `2` reports native search errors. For example,
`cat file | grep pattern | wc -l` counts matching lines, and `rg pattern src ||
tee fallback` runs the fallback after no matches or another search failure.
The existing timeout, output-size, and filesystem-race boundaries still apply.

### Discover files and directories

`ls` and `find` use native GNU output and expression behavior. Both default to
the configured base, ignore stdin, and can pipe their output to another command.
Relative operand spelling is retained, including `./`, trailing slashes, and
`..`; this matters for `find . -path './src/*'` and for printed filenames.
Tagged paths that could be mistaken for options or expressions receive a `./`
prefix. Missing literal roots produce native errors; unmatched PTH patterns fail
before execution, matching zsh's default.

`ls` accepts the following flags, including native combinations such as `-lah`,
repeated flags, and flags interspersed with paths. Native option precedence is
preserved. After `--`, only PTH operands are accepted.

| Purpose | Options |
| --- | --- |
| Details | `-l`, `-h` / `--human-readable`, `-n` / `--numeric-uid-gid`, `-i` / `--inode`, `-s` / `--size` |
| Selection | `-a` / `--all`, `-A` / `--almost-all`, `-d` / `--directory`, `-R` / `--recursive` |
| Ordering and display | `-1`, `-r` / `--reverse`, `-t`, `-S`, `-U`, `-F` / `--classify`, `-p` |

For `find`, put PTH roots first, then the expression. Optional `-P` and `--` may
precede roots. Predicates and operators use FLG, including `(`, `)`, and `!`;
values use ARG. Only CTL tokens separate commands. Do not add shell quotes or
backslashes around expression tokens.

| Purpose | Predicates and operators |
| --- | --- |
| Matching, followed by ARG | `-name`, `-iname`, `-path`, `-ipath`, `-type`, `-size`, `-mtime`, `-mmin` |
| Traversal | `-maxdepth` / `-mindepth` followed by ARG, `-depth`, `-xdev` |
| Tests and output | `-empty`, `-print`, `-print0`, `-prune`, `-quit` |
| Expressions | Implicit AND, `-a` / `-and`, `-o` / `-or`, `!` / `-not`, `(` and `)` |

Native find evaluates precedence, short-circuiting, pruning, repeated predicates,
numeric values, and malformed expressions. Its default action is printing matches.
Predicate ARG patterns are passed literally and support native `*`, `?`, and
bracket matching independently of PTH expansion. For example, skip a directory:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "List paths while skipping the cache directory.",
  "value": [
    ["find", "CMD"], [".", "PTH"],
    ["-name", "FLG"], ["cache", "ARG"], ["-prune", "FLG"],
    ["-o", "FLG"], ["-print", "FLG"]
  ]
}
```

As in v1, READ is checked **only on supplied or expanded roots**, or the base when
omitted. Every root check and approval completes before execution. A denied root
prevents the whole command. Directory authorization permits native discovery
beneath it: descendant deny/ask rules and descendant file-type restrictions are
not applied. There is no recursive policy pre-scan. Explicit roots still reject
symlinks, hard-linked regular files, and special files; descendants may be listed
as metadata, and native traversal does not follow symlinks. `ls -a` also lists
`.` and `..` using native behavior under the directory authorization.

Unknown options are rejected. In particular, `find` cannot execute commands,
delete files, write output files, read indirect root lists, follow symlinks, or
use predicates that access additional reference paths. The supported flags above
are an allowlist, not a complete implementation of every GNU option.

### Create and update files

`tee` writes stdin to each destination and to stdout. It creates missing files
and overwrites existing files by default; `-a` / `--append` appends instead.
Destinations are literal PTH values and reject wildcards. Each destination must
be a regular file or a missing file with an existing parent directory.

One optional ARG after flags and before destination paths supplies inline text:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Append a note.",
  "value": [
    ["tee", "CMD"], ["-a", "FLG"],
    ["Hello\n", "ARG"], ["notes.txt", "PTH"]
  ]
}
```

Inline text is encoded as UTF-8 when the subprocess starts, preserving whitespace
and line endings; it is stdin, not an executable argument. Incoming pipe bytes override inline text,
including empty output. With neither, stdin is empty and `tee` never waits for
terminal input. With no destinations, `tee` only produces stdout and requires no
filesystem permissions. For example, `tee` with a content ARG can feed `wc` via
`["|", "CTL"]`. Inline text is omitted from command labels, including failure
labels; successful `tee` still returns its content on stdout. Reports use the
prepared command's label, including on permission denial. If preparation fails,
the label is just the command name, with the error reported in the body.

`touch` requires at least one target PTH. It creates missing named files empty,
unless `-c` / `--no-create` is supplied, and updates timestamps on existing regular
files and directories. Directory timestamps are updated without recursion.
New files require existing parents; neither command creates parent directories.

| Command | Supported options |
| --- | --- |
| `tee` | `-a` / `--append`, `--` |
| `touch` | `-a` (access time), `-m` (modification time), `-c` / `--no-create`, `-d` / `--date` followed by a date ARG, `-t` followed by a `[[CC]YY]MMDDhhmm[.ss]` ARG, `-r` / `--reference` followed by PTH, `--` |

Both use separate FLG tokens before operands. Unknown, repeated, bundled, and
attached options are rejected. `touch -a -m` updates both timestamps. `-r` and
`-d` can be combined for dates relative to reference timestamps; `-t` conflicts
with either. Native `touch` interprets dates and calendar values. Reference paths
must be literal existing regular files or directories and require READ.

For example, create a file using another file's timestamps:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Create a file with the reference timestamps.",
  "value": [
    ["touch", "CMD"], ["-r", "FLG"], ["reference.txt", "PTH"],
    ["new.txt", "PTH"]
  ]
}
```

`touch` targets also support the patterns described below. **If a pattern matches
nothing, the command fails before execution, even with `-c`. It does not create
a filename containing the unmatched wildcard.** This matches zsh's default
and Bash with `failglob`, rather than default Bash. A missing named target such
as `new.txt` is still created normally, or left missing with `-c`.

Both commands require CREATE on every target. Existing regular files additionally
require READ and DELETE, including append and timestamp updates, preserving v1's
permission policy. Missing `touch -c` targets remain subject to CREATE permission.
All target checks and approvals complete before any native writes. Symlinks,
hard-linked regular files, and special files are unsupported. Target order and
repeats are preserved while permissions and approvals are deduplicated.
`["-", "PTH"]` names the literal file `-` for both commands.

### Create directories

`mkdir` requires one or more literal directory PTH operands. It accepts
`-p` / `--parents` to create missing parents and accept existing directories,
`-v` / `--verbose` to report each created directory, and `--` to end options.
Flags must be separate FLG tokens before operands. Wildcards, ARG operands,
unknown, repeated, bundled, and attached options are rejected. Explicit modes
(`-m` / `--mode`) and security-context options are unsupported; native default
directory permissions apply.

Create a directory tree, then write a note after successful creation:

```json
{
  "action": "run_tagged_file_command",
  "rationale": "Create the daily notes directory and write today's note.",
  "value": [
    ["mkdir", "CMD"], ["-p", "FLG"], ["notes/daily", "PTH"],
    ["&&", "CTL"],
    ["tee", "CMD"], ["Hello\n", "ARG"], ["notes/daily/today.txt", "PTH"]
  ]
}
```

Every explicit directory target requires CREATE, even when it already exists.
With `-p`, every missing parent also requires CREATE; existing intermediate
directories require no additional permissions. For example, if `notes` is
missing, the call above checks CREATE on both `notes` and `notes/daily` before
running `mkdir`. Neither READ nor DELETE is required for directory creation.
Checks and approvals are deduplicated and complete before any directory is
created; a denied target or parent prevents the entire command.

Path spelling and operand order are preserved, including repeated targets,
trailing slashes, `/.`, and `/..`. `mkdir -p a/../b` can create both `a` and `b`,
so both require CREATE. Symlinks and existing non-directory components reject
the command before any writes, including components before or after `..`.
Leading dashes, spaces, and backslashes in PTH names remain literal.

Without `-p`, `mkdir a a/b` creates the parent before its child. Existing
directories and missing parents produce native failures, and a failed
multi-operand command can still create some directories. Earlier effects are
preserved, and the native exit status selects `&&` or `||` continuations.

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
separate chain field. The input types and schema restrict CMD values to the
supported commands and CTL values to the four supported operators. Every segment starts with CMD;
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

Stored sequence history and the final report are capped at 40,000 characters,
retaining the newest output and marking omitted earlier output. The final status
counts toward that budget; piped stdout is passed intact.

Pipes are buffered and sequential so every stage is guarded before execution.
They preserve stdout exactly, including empty output, and keep stderr separate.
Reports decode output as UTF-8 and show undecodable bytes as `\xNN` escapes;
these display escapes do not change the bytes passed to the next pipeline stage.
The last pipeline stage determines its status (no `pipefail`). The current
`cp`/`mv` commands do not consume stdin. There is no rollback of earlier writes.

### Source patterns

Use `*` within source-path components and one standalone recursive `**` component:

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
`src/*`, `src/*.py`, and `report-*-final.*` are supported. Repeated stars inside
ordinary components, such as `report**.py`, also stay within one component.
Hidden names require an explicitly leading dot in each component: `src/*`
excludes them and `src/.*` includes them. Wildcards never select the special `.`
or `..` directory entries.
These follow default [zsh filename generation](https://zsh.sourceforge.io/Doc/Release/Expansion.html#Filename-Generation).

A standalone `**/` follows zsh's default recursion rules. It matches zero or more
directory levels, so `src/**/*.py` includes direct children of `src` and
`**/file.py` includes `file.py` in the current directory. A final `**` without a
slash behaves like `*`: `sdf/**` selects only immediate visible children, while
`sdf/**/*` selects visible descendants. `sdf/**/` selects directories, including
`sdf/`; bare `**/` excludes the implicit current directory. Recursive traversal
skips hidden names; an explicit component such as `src/.hidden/**/*.py` can
select files within a hidden directory. Only one recursive `**/` component is
supported per PTH; a final `**` does not count as a recursive component.
Symlink-following `***/` patterns are unsupported.

Source operands retain their order; each pattern's matches are sorted in
C-locale filesystem byte order. If any pattern has no matches, that command
fails before execution. This matches zsh's default or Bash with `failglob`,
not default Bash's treatment of an unmatched pattern as a literal filename.
Missing or non-directory branches contribute no
matches; symlinks, permission errors, and other traversal errors reject the
command. Matched filenames are literal executable arguments, including spaces,
leading dashes, and wildcard characters; they are never expanded again.

Destinations, both the last positional PTH and `-t` values, remain literal and
reject `*`. `?` and bracket patterns are unsupported in all input paths.
There is no shell quoting, variable expansion, or command substitution.
Multiple-source and `-T` constraints apply to the expanded source count.
For transfers, duplicate sources, conflicting destinations, source/destination
overlap, and unsupported matched entries reject the entire command. Every match
receives the existing permission checks. Selected transfer directories require checks for every
descendant, including hidden entries that the pattern itself does not select.

Ancestor/descendant sources are allowed for literal and patterned operands when
their destination mappings are separate. For example, `cp -R sdf/ sdf/**/* out` can
copy `sdf/` and each visible descendant to distinct locations in an existing
`out` directory. Repeated basenames can still cause destination conflicts.
The approved argv executes in its original order: `mv sdf/ sdf/**/* out` may move
`sdf/` first, then fail because its descendant operands no longer exist. These
partial effects are preserved, and the native failure status controls `&&` and
`||` continuations.

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

## Transfer command subset

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
| `src/**/**/*.py`, `?.txt`, `[ab].txt`, or a destination containing `*` | Multiple recursive components, unsupported input patterns, or a nonliteral destination |
| A source pattern with no matches | All operands must resolve before execution |
| A symlink in a source, destination, or path component | Symlinks are unsupported |
| A source or existing destination file with more than one hard link | Hard-linked files are unsupported for both cp and mv |
| Duplicate sources, overlapping outputs, a source/destination overlap, or a destination aliasing a source | Conflicting transfers |
| Moving a directory onto a populated effective destination directory | Native `mv` does not merge directories |
| A move across filesystems | Copy-and-delete fallback is outside this prototype |

## Boundaries and review

This is an experiment, not an OS sandbox. Filesystem races and broader command
support remain out of scope. Commands retain the existing timeout and output
limits; native execution can partially complete a multi-file transfer
before failing. The tagged input is retained through resolution and guarding.
The tagged executor runs the prepared argv directly. The public input
contains only the tagged value; its validator checks the command/CTL grammar.
The executor consumes the remaining tokens directly, using the latest exit
status to skip or select the next command.
The guard transports the execution payload through its existing original-input
field: the prepared command, remaining tokens, pipe input, accumulated output,
and preparation errors. The prepared command is independent of permission items,
allowing stdin-only readers to execute with no filesystem requirements. Each
reached command clears the previous payload before preparation.
The original request is retained in full. Both entry and continuation use the
same step preparation; there is no stored list of parsed commands or operators.

The existing CLI tools and default capabilities are unchanged. Inspect
`contracts.py`, `sequence.py`, `command.py`, `guard.py`, and `execute.py` to review
the contract and guarded loop. Run `uv run pytest tests/shed/test_tagged*.py` for
scripted agent calls that read, write, copy, and move temporary files through the full chain.
