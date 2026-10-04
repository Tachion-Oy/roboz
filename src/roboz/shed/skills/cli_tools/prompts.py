"""Usage and worked examples for guarded file commands."""

from typing import Final

from roboz.shed.identifiers import RUN_FILE_COMMAND_TOOL_NAME

DESCRIPTION: Final[str] = (
    f"Use `{RUN_FILE_COMMAND_TOOL_NAME}` for guarded file discovery, searching, reading, "
    "writing, transfers, and deletion. Covers token roles, command options, path "
    "permissions, patterns, and mixed command chains with worked examples."
)

_INSTRUCTIONS_TEMPLATE = r"""## File CLI (`<<RUN_FILE_COMMAND_TOOL>>`)

Use this tool to discover, read, search, compare, create, update, copy, move, or
remove files. Paths may be absolute or relative to the configured working
directory. That directory is a path shorthand; authorization comes from the
deployment's allow/deny/ask rules and default verdict.

### Input and choosing commands

Supply one `value` array of ordered `[value, tag]` pairs:

| Tag | Meaning |
| --- | --- |
| `CMD` | Command name; begins each command. |
| `FLG` | Flag, option, or a `find` predicate/operator. |
| `ARG` | Non-path argument: pattern, count, date, or inline `tee` content. |
| `PTH` | File or directory path, including supported path patterns. |
| `CTL` | One of `&&`, `||`, `;`, or `|`, between commands. |

Each JSON example below is one tool payload. Add the agent's usual `action`
(`<<RUN_FILE_COMMAND_TOOL>>`) and `rationale` fields when invoking it. Send one
JSON object per invocation; several commands belong in the same `value` array.
Values are literal strings: do not add shell quoting around spaces or patterns.
Only CTL tokens separate commands; `['&&', 'PTH']` names a literal file.

Prefer `find`/`ls` for discovery, `rg` for text search (`grep` is also available),
`wc -l` for size, and `head`/`tail` for bounded reading. Use `cat` for whole files,
`diff` for comparison, `mkdir`/`touch`/`tee` for creation, `cp`/`mv` for transfers,
and `gio trash`/`rm` for deletion. Use the file-editing skill for literal patches.
The command set is fixed; Git and arbitrary programs are unavailable.

## Chaining and pipelines

Mix operators freely within one call. The supported operators follow zsh's
control-flow rules:

- `A && B`: run B after A succeeds (exit status 0).
- `A || B`: run B after A fails (nonzero status).
- `A ; B`: run B after either ordinary outcome of A.
- `A | B`: pass A's exact stdout bytes to B's stdin; stderr stays separate.

Pipelines bind first. `&&` and `||` have equal precedence and associate left to
right. Consequently `A || B && C` runs C after either A or B succeeds; it is not
an if/else expression. The final pipeline stage determines pipeline status
(no `pipefail`); the last executed command determines the call's final status.
Empty stdout can still mean success. Search and diff decisions use exit status.
See [zsh's grammar](https://zsh.sourceforge.io/Doc/Release/Shell-Grammar.html#Simple-Commands-_0026-Pipelines).

Each reached command is prepared and guarded afresh, so later commands see
files created earlier. Skipped commands and entire skipped pipelines neither
expand paths nor request approvals. Preparation errors and permission denials
are failed steps: `||` can select a fallback, which must pass its own checks.
All permission checks for one command pass before any approvals or execution.

Pipelines run sequentially with buffered stdout, rather than concurrently.
There is no shell parser, grouping, redirection, variable/command substitution,
background execution, or streaming/SIGPIPE behavior. Only pipe input replaces
inline `tee` content, even when the incoming bytes are empty. Without a pipe,
readers receive empty stdin unless they name files.

Missing executables return 127, permission/launch failures 126, and timeouts 124.
Timeouts discard partial output and participate in normal control flow, including
pipelines. An ordinary failed pipeline stage supplies its stdout (empty for a
preparation error, denial, or timeout) to the next stage. Invalid sequence grammar
rejects the whole call. Cancellation, oversized output, and unexpected execution
errors stop the sequence, including fallbacks. Earlier writes are never rolled back.

## Worked chains

The command expressions explain intent; the JSON is what the tool accepts.

### Dependent steps

`mkdir -p notes && touch notes/today.txt`: create the file only after the
directory command succeeds.

```json
{"value": [["mkdir", "CMD"], ["-p", "FLG"], ["notes", "PTH"], ["&&", "CTL"], ["touch", "CMD"], ["notes/today.txt", "PTH"]]}
```

### Fallback read

`cat primary.txt || cat backup.txt`: read the backup if the primary is missing,
unreadable, or denied. The backup is independently authorized; fallback does not
bypass the primary's denial. After a successful primary read, the backup is skipped.

```json
{"value": [["cat", "CMD"], ["primary.txt", "PTH"], ["||", "CTL"], ["cat", "CMD"], ["backup.txt", "PTH"]]}
```

### Independent inspections

`wc -l report.txt ; head -n 20 report.txt ; tail -n 20 report.txt`: inspect size,
start, and end. Each step runs after the previous step's ordinary success or failure.

```json
{"value": [["wc", "CMD"], ["-l", "FLG"], ["report.txt", "PTH"], [";", "CTL"], ["head", "CMD"], ["-n", "FLG"], ["20", "ARG"], ["report.txt", "PTH"], [";", "CTL"], ["tail", "CMD"], ["-n", "FLG"], ["20", "ARG"], ["report.txt", "PTH"]]}
```

### Pipeline window

`head -n 80 report.txt | tail -n 20 | cat -n`: read lines 61–80 when the file
has at least 80 lines, then number that window from 1. Starting with `head` also
bounds the captured output before piping; piping a huge `cat` into `head` would
still capture all of `cat` first.

```json
{"value": [["head", "CMD"], ["-n", "FLG"], ["80", "ARG"], ["report.txt", "PTH"], ["|", "CTL"], ["tail", "CMD"], ["-n", "FLG"], ["20", "ARG"], ["|", "CTL"], ["cat", "CMD"], ["-n", "FLG"]]}
```

### Mixed operators

`mkdir -p out && head -n 20 source.txt | tee out/preview.txt || cat fallback.txt ; wc -l source.txt`

Create the directory, then run the preview pipeline if mkdir succeeds. The pipeline
status is tee's status: if head fails but tee succeeds, the fallback is skipped.
The fallback runs when mkdir or the pipeline fails. The final wc runs after
either ordinary outcome and determines the call's final status. Use separate
`&&` steps when each file operation must succeed before a later write.

```json
{"value": [["mkdir", "CMD"], ["-p", "FLG"], ["out", "PTH"], ["&&", "CTL"], ["head", "CMD"], ["-n", "FLG"], ["20", "ARG"], ["source.txt", "PTH"], ["|", "CTL"], ["tee", "CMD"], ["out/preview.txt", "PTH"], ["||", "CTL"], ["cat", "CMD"], ["fallback.txt", "PTH"], [";", "CTL"], ["wc", "CMD"], ["-l", "FLG"], ["source.txt", "PTH"]]}
```

### Left-to-right conditions

`cp source.txt preferred.txt || cp source.txt fallback.txt && cat source.txt`

A successful first copy skips the second copy and still runs cat. If the first
copy fails, the fallback copy runs, and cat runs only if that copy succeeds.

```json
{"value": [["cp", "CMD"], ["source.txt", "PTH"], ["preferred.txt", "PTH"], ["||", "CTL"], ["cp", "CMD"], ["source.txt", "PTH"], ["fallback.txt", "PTH"], ["&&", "CTL"], ["cat", "CMD"], ["source.txt", "PTH"]]}
```

### Skipped pipeline

`cat primary.txt || cat backup.txt | head -n 20`: a successful primary read skips
both fallback stages. Otherwise the backup feeds head, whose status determines
the fallback pipeline's status. Its success alone does not prove that backup cat
succeeded; inspect the individual command frames as well.

```json
{"value": [["cat", "CMD"], ["primary.txt", "PTH"], ["||", "CTL"], ["cat", "CMD"], ["backup.txt", "PTH"], ["|", "CTL"], ["head", "CMD"], ["-n", "FLG"], ["20", "ARG"]]}
```

### Fresh path expansion

`mkdir -p out && cp source.txt out/new.txt && cat out/*.txt`: the final pattern
is expanded only after the copy succeeds, so it includes the newly created file.

```json
{"value": [["mkdir", "CMD"], ["-p", "FLG"], ["out", "PTH"], ["&&", "CTL"], ["cp", "CMD"], ["source.txt", "PTH"], ["out/new.txt", "PTH"], ["&&", "CTL"], ["cat", "CMD"], ["out/*.txt", "PTH"]]}
```

## Command reference

Except for ls/find, place separate flags before operands. `--` ends options.
Unknown, repeated, bundled, and attached options are rejected, except where
explicitly supported below. Option values have their own ARG or PTH token.

### Reading and comparison

- `pwd`: physical base directory; optional `-P`/`--physical`; no operands.
- `cat`: `-n`/`--number`, `-b`/`--number-nonblank` (overrides -n),
  `-s`/`--squeeze-blank`, `-E`/`--show-ends`, `-T`/`--show-tabs`.
- `head`/`tail`: ten lines by default; `-n`/`--lines` or `-c`/`--bytes` followed
  by an unsigned decimal ARG (zero allowed). `-q`/`--quiet`/`--silent` conflicts
  with `-v`/`--verbose`; line and byte counts conflict. Signed counts, size
  suffixes, and follow mode are unsupported.
- `wc`: combine `-l`/`--lines`, `-w`/`--words`, `-c`/`--bytes`, `-m`/`--chars`,
  and `-L`/`--max-line-length`; indirect file lists are unsupported.
- `diff`: exactly two regular-file PTHs after expansion, preserving order and
  repeats; `-u`/`--unified`, `-q`/`--brief`, `-s`/`--report-identical-files`,
  `-i`/`--ignore-case`, `-w`/`--ignore-all-space`. Status 0 means equal, 1
  different, 2 error. It never reads stdin.

Readers require READ on selected regular files and preserve operand order and
repeats. Directories, symlinks, hard-linked files, and special files are rejected.
Omitted reader operands or `['-', 'ARG']` consume stdin; `['-', 'PTH']` names the
literal file. pwd requires READ on the base.

### Text search

`grep`/`rg` take one pattern ARG before input PTHs or stdin `['-', 'ARG']`.
Patterns go literally to the native regex engine: do not shell-escape them.
Both support `-F`/`--fixed-strings`; grep also supports `-E`/`--extended-regexp`
(conflicting with -F). Both accept `-n`/`--line-number`, `-i`/`--ignore-case`,
`-v`/`--invert-match`, `-w`/`--word-regexp`, `-x`/`--line-regexp`, `-c`/`--count`,
`-l`/`--files-with-matches`, `-q`/`--quiet`, `-o`/`--only-matching`, and
`-H`/`--with-filename`. Filename suppression is grep `-h`/`--no-filename` or
rg `-I`/`--no-filename`, conflicting with -H. `-m`/`--max-count`,
`-A`/`--after-context`, `-B`/`--before-context`, and `-C`/`--context` take unsigned
decimal ARG values. Statuses are 0 for a match, 1 for no matches, 2 for errors.

Without paths they read stdin, except recursive grep searches the base.
grep directory inputs require `-r`/`--recursive` or `-R`/`--dereference-recursive`.
rg searches directories recursively and respects local/ancestor `.gitignore`,
`.ignore`, and `.rgignore`. Explicit files override ignore filtering; explicitly
naming a directory does not disable ignores. rg supports `--hidden`, `--no-ignore`,
and the single bundled flag `-uu` (hidden files plus no ignores). Configuration,
global Git ignores, and Git info/exclude files are disabled.

Recursive searches require READ on the entire candidate tree, including hidden
and ignored files; rg additionally checks potential local/ancestor ignore files
unless ignores are disabled. An ignored file can still block authorization.
Explicit symlinks and hard-linked/special inputs are rejected. Recursive grep -r
and rg skip descendant symlinks and special files; grep -R rejects them.
Glob matches are explicit inputs: one matched symlink rejects the whole command
before any file content is read. Every name of a file with multiple hard links
is rejected; there is no privileged original name.
Pattern files, preprocessors, and other executable options are unsupported.

### Search with alternation

`rg -n 'TODO|FIXME' src`: regex alternation stays inside one ARG token.

```json
{"value": [["rg", "CMD"], ["-n", "FLG"], ["TODO|FIXME", "ARG"], ["src", "PTH"]]}
```

### Discovery

`ls` defaults to the base and accepts `-l`, `-a`/`--all`, `-A`/`--almost-all`,
`-h`/`--human-readable`, `-d`/`--directory`, `-R`/`--recursive`, `-1`,
`-r`/`--reverse`, `-t`, `-S`, `-U`, `-F`/`--classify`, `-p`, `-i`/`--inode`,
`-s`/`--size`, and `-n`/`--numeric-uid-gid`. Native ordering, repetitions, and
bundled flags such as `-lah` work; after `--` only PTHs are allowed.

`find` takes PTH roots first (default `.`), then FLG predicates/operators and
ARG values. It supports `-name`, `-iname`, `-path`, `-ipath`, `-type`, `-size`,
`-mtime`, `-mmin`, `-maxdepth`, `-mindepth` with values; `-empty`, `-depth`,
`-xdev`, `-print`, `-print0`, `-prune`, `-quit`; implicit AND, `-a`/`-and`,
`-o`/`-or`, `!`/`-not`, and parentheses as FLGs. Optional `-P` or `--` may
precede roots. ARG patterns use native *, ?, and bracket matching, without PTH
expansion. Preserve relative roots for expressions such as `-path './src/*'`.

ls/find require READ on explicit/expanded roots, or the base if omitted.
Directory authorization permits discovery underneath it; descendant deny/ask
rules and descendant file-type restrictions do not apply. Explicit roots reject
symlinks, hard-linked files, and special files. Native traversal does not follow
symlinks. Both ignore stdin and can pipe output. Execution, deletion, file-output
actions, indirect root lists, and symlink-following options are unsupported.

### Find by name

`find . -name '*.py' -print`: the pattern is ARG, while the root is PTH.

```json
{"value": [["find", "CMD"], [".", "PTH"], ["-name", "FLG"], ["*.py", "ARG"], ["-print", "FLG"]]}
```

### Writing and directory creation

`tee` writes stdin to each literal destination PTH and stdout, creating or
overwriting files; `-a`/`--append` appends. One optional ARG after flags and before
paths supplies inline UTF-8 text. Pipe bytes override it, including empty output.
With neither source, stdin is empty. Without paths, tee needs no file permissions.

`touch` requires target PTHs. It creates missing files and updates timestamps on
existing regular files/directories without recursion. `-c`/`--no-create` skips
missing files, but still requires CREATE. Options: `-a` (access time), `-m`
(modification time), `-d`/`--date` plus date ARG, `-t` plus
`[[CC]YY]MMDDhhmm[.ss]` ARG, and `-r`/`--reference` plus literal existing PTH
requiring READ. Combine -a/-m and -r/-d; -t conflicts with -d/-r. Native touch
interprets dates. Target patterns must match even with -c; references are literal.

tee/touch require CREATE on targets and also READ and DELETE on existing regular
files, including append and timestamp updates. Parents must already exist.

`mkdir` creates literal directory PTHs. Options: `-p`/`--parents`,
`-v`/`--verbose`, and `--`; mode/security-context options are unsupported.
CREATE is required on every explicit target, including existing directories,
and each missing parent created by -p. Existing intermediate directories need no
extra permission. `mkdir -p a/../b` can create both a and b and checks both.
Symlinks or non-directory components reject the command. Operand order/repeats
are preserved, so `mkdir a a/b` works without -p. Native failures may retain
partial effects; use && for dependent work.

### Inline content

Create notes, write two lines, and read them back. The content is one ARG,
including its newlines; the destination is PTH.

```json
{"value": [["mkdir", "CMD"], ["-p", "FLG"], ["notes", "PTH"], ["&&", "CTL"], ["tee", "CMD"], ["line 1\nline 2\n", "ARG"], ["notes/today.txt", "PTH"], ["&&", "CTL"], ["cat", "CMD"], ["notes/today.txt", "PTH"]]}
```

### Transfers

`cp`/`mv` take PTH operands; without -t the final path is the destination.
cp requires `-r`/`-R`/`--recursive` for directories; mv moves directories without
that flag. Both accept `-v`/`--verbose`, `-f`/`--force`,
`--strip-trailing-slashes`, `-t`/`--target-directory` plus a literal PTH directory,
`-T`/`--no-target-directory`, and `--`. -t and -T conflict; -T requires one
expanded source and an exact destination. Multiple sources need a directory.
cp merges directories; `src/.` copies contents into the destination. mv can
replace an empty directory but cannot merge populated directories.

cp requires READ on sources; mv requires DELETE. Destinations require CREATE;
overwrites and empty-directory replacement also require READ and DELETE there.
Permissions cover all descendants, including hidden files and empty directories.
Copy merges require CREATE on existing directories without DELETE. Force never
bypasses permissions or approval. Symlinks, hard-linked/special files,
duplicate sources, conflicting destinations, overlapping source/destination
paths, and cross-filesystem moves are rejected. Ancestor/descendant sources with
separate outputs are allowed; native mv can move the parent then fail on a
vanished descendant. Native partial effects and exit statuses are preserved.

### Deletion

`gio` supports only `['trash', 'ARG']` then target PTHs, without options.
`rm` supports separate `-r`/`-R`/`--recursive`, `-f`/`--force`, and `--`
before PTHs. DELETE is required on named entries; gio trash and recursive rm
also check all descendants, including hidden entries, before execution.
Terminal symlinks (even dangling links and glob matches) are deleted at their own
path, never followed. Hard links and special entries can also be removed.
Symlink parents are rejected, including a trailing slash or dot component after
a link: remove links by bare pathname. Native suffixes, operand order, repeats,
missing-target and no-operand behavior are preserved. Missing named targets still
require DELETE; -f never bypasses policy. Unmatched patterns fail even with -f.
gio trash with no targets checks DELETE on the base before its native error.

## Paths, patterns, and permissions

PTH patterns support `*` within components and one recursive `**/` component.
`src/**/*.py` includes files directly under src and deeper. Following default
zsh, final `**` acts like `*`: `src/**` selects immediate visible children,
`src/**/*` selects descendants, and `src/**/` selects directories including src.
Bare `**/` excludes the implicit current directory. Hidden names require an
explicit leading dot in their component; recursion skips them. Repeated stars
inside ordinary components are nonrecursive. Matches sort in filesystem byte
order per operand; zero matches fail before execution. Multiple recursive
components, `***/`, `?`, and bracket patterns are unsupported in PTH tokens.
Transfer/tee destinations, mkdir targets, and touch references must be literal.
Trailing `/` selects directories; `/.` and `/..` retain native meaning.

Permission-rule patterns are separate from PTH expansion: relative rules match
within the configured base, absolute rules can match outside it. Literal POSIX
names retain spaces and backslashes. Allow/deny conflicts use the configured
precedence; unmatched operations use the default verdict. Ask rules apply to
each required operation. Approvals and permissions are deduplicated per command;
a denial prevents all approvals and execution. Missing/declined approval fails
the step. Tool reports include each approval question, the user's exact reply,
and the decision, even if the approved operation later fails. These records are
report text, never piped stdout. Read the user's reply before choosing your next
action; successful execution alone does not mean that no prompt occurred.
Preparation errors, such as a missing parent, may precede policy checks and are
not policy-denial verdicts. The base directory alone grants no access. Recursive
search preflight and deletion preparation have 60-second deadlines; deletion includes pattern
expansion in its deadline. Each native command has a separate 60-second timeout.

## Keeping output useful

Per-command stdout plus stderr over 100,000 decoded characters stops the whole
sequence without returning partial command output. Narrow paths/patterns, use
`rg --count` or `--max-count`, or begin a read with head/tail. A downstream head
does not prevent an upstream command from exceeding the capture limit.

The final multi-command report retains the newest output within 40,000 characters
and explicitly marks omitted earlier output. This report limit does not change
bytes passed through pipes. Non-UTF-8 bytes are escaped for display only. Inspect
individual frames as well as the final status, especially after fallbacks or
pipelines. File changes from earlier commands remain after subsequent failures.
"""

INSTRUCTIONS: Final[str] = _INSTRUCTIONS_TEMPLATE.replace(
    "<<RUN_FILE_COMMAND_TOOL>>", RUN_FILE_COMMAND_TOOL_NAME
)
