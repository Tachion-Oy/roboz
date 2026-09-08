"""Argv path-index extraction for guarded CLI commands."""

from __future__ import annotations

from collections.abc import Callable

from ._argv import Arguments, parse_options

type PathExtractor = Callable[[list[str]], list[int]]


def resolve_path_indices(argv: list[str], extractor: PathExtractor | None) -> list[int]:
    """Return sorted, in-range path indices from a command's extractor."""
    if extractor is None:
        return []
    return sorted(i for i in extractor(argv) if 0 <= i < len(argv))


def _search_paths(argv: list[str], parsed: Arguments) -> list[int]:
    explicit_pattern = parsed.options & {"-e", "--regexp", "-f", "--file"}
    listing = parsed.options & {"--files", "--type-list"}
    operands = parsed.positionals
    if not explicit_pattern and not listing:
        operands = operands[1:]
    return [i for i in operands + parsed.path_values if argv[i] != "-"]


def grep_path_args(argv: list[str]) -> list[int]:
    """Return grep inputs, distinguishing patterns, option values, and files.

    Options may follow operands. ``--`` ends options, and ``-e``
    removes the positional pattern. Pattern files and other auxiliary inputs are
    rejected along with unknown options before execution.
    """
    return _search_paths(
        argv,
        parse_options(
            argv,
            flags="-E -F -G -P -i -w -x -z -s -v -V -b -n -H -h -o -q -a -I -r -R -L -l -c -T -Z -U "
            "--extended-regexp --fixed-strings --basic-regexp --perl-regexp "
            "--ignore-case --no-ignore-case --word-regexp --line-regexp --null-data "
            "--no-messages --invert-match --byte-offset --line-number --line-buffered "
            "--with-filename --no-filename --only-matching --quiet --silent --text "
            "--recursive --dereference-recursive --files-without-match --files-with-matches "
            "--count --initial-tab --null --no-group-separator --binary",
            values="-e --regexp -m --max-count -A --after-context -B --before-context "
            "-C --context -d --directories -D --devices --label --binary-files "
            "--include --exclude --exclude-dir --group-separator",
            optional_values="--color --colour",
            numeric=True,
        ),
    )


def rg_path_args(argv: list[str]) -> list[int]:
    """Return ripgrep search inputs, distinguishing patterns from file operands.

    ``--files`` has no positional pattern. Separate and attached non-path option
    values are supported; auxiliary files and subprocess preprocessors are not.
    """
    return _search_paths(
        argv,
        parse_options(
            argv,
            flags="-i -s -S -F -P -U -w -x -v -a -n -N -H -I -l -0 -c -o -q -b -L -u -h -V "
            "--ignore-case --case-sensitive --smart-case --fixed-strings --pcre2 "
            "--multiline --multiline-dotall --word-regexp --line-regexp --invert-match "
            "--text --line-number --no-line-number --with-filename --no-filename "
            "--files-with-matches --files-without-match --null --count --count-matches "
            "--only-matching --quiet --byte-offset --follow --hidden --no-ignore "
            "--no-ignore-dot --no-ignore-exclude --no-ignore-files --no-ignore-global "
            "--no-ignore-parent --no-ignore-vcs --no-require-git --no-messages "
            "--no-config --no-unicode --unicode --crlf --null-data --no-mmap "
            "--mmap --line-buffered --block-buffered --heading --no-heading "
            "--trim --stats --json --files --type-list --debug --trace --no-pre "
            "--no-search-zip --one-file-system --passthru --include-zero",
            values="-e --regexp -m --max-count -A --after-context -B --before-context "
            "-C --context -g --glob --iglob -t --type -T --type-not -j --threads "
            "-M --max-columns -r --replace -E --encoding --color --colors "
            "--context-separator --field-context-separator --field-match-separator "
            "--path-separator --max-depth --maxdepth --max-filesize --sort --sortr "
            "--engine --regex-size-limit --dfa-size-limit --type-add --type-clear",
        ),
    )


def cat_path_args(argv: list[str]) -> list[int]:
    """Return cat operands anywhere in argv, preserving ``-`` as stdin."""
    return parse_options(
        argv,
        stdin=True,
        flags="-A -b -e -E -n -s -t -T -u -v --show-all --number-nonblank "
        "--show-ends --number --squeeze-blank --show-tabs --show-nonprinting",
    ).positionals


def head_path_args(argv: list[str]) -> list[int]:
    """Return head files, excluding line/byte counts and the stdin marker."""
    return parse_options(
        argv,
        stdin=True,
        flags="-q -v -z --quiet --silent --verbose --zero-terminated",
        values="-n -c --lines --bytes",
    ).positionals


def tail_path_args(argv: list[str]) -> list[int]:
    """Return tail files, excluding option values and the stdin marker."""
    return parse_options(
        argv,
        stdin=True,
        flags="-f -F -q -v -z --retry --quiet --silent --verbose --zero-terminated",
        values="-n -c -s --lines --bytes --sleep-interval --pid --max-unchanged-stats",
        optional_values="--follow",
    ).positionals


def find_path_args(argv: list[str]) -> list[int]:
    """Return find roots from the supported read expression.

    Global options precede roots; expression arguments are parsed separately.
    Unknown predicates, reference-file inputs, actions that execute or write,
    and indirect root lists are rejected. Use ``./-name`` for a root beginning with a dash.
    """
    i = 0
    while i < len(argv) and argv[i] in {"-H", "-L", "-P", "--"}:
        i += 1
    indices: list[int] = []
    while i < len(argv) and not argv[i].startswith("-") and argv[i] not in {"!", "("}:
        indices.append(i)
        i += 1
    flags = set(
        "( ) ! , -not -a -and -o -or -daystart -follow -depth -mount -noleaf "
        "-xdev -ignore_readdir_race -noignore_readdir_race -empty -false "
        "-nouser -nogroup -readable -writable -executable -true -print "
        "-print0 -ls -prune -quit --help --version".split()
    )
    values = set(
        "-regextype -maxdepth -mindepth -amin -atime -cmin -ctime -fstype "
        "-gid -group -ilname -iname -inum -iwholename -iregex -links "
        "-lname -mmin -mtime -name -path -perm -regex -wholename -size "
        "-type -uid -used -user -xtype -context -printf".split()
    )
    while i < len(argv):
        token = argv[i]
        if token in values:
            i += 1
            if i == len(argv):
                raise ValueError(f"Option {token!r} requires a value")
        elif token not in flags:
            raise ValueError(
                f"Forbidden pattern or unsupported find argument: {token!r}"
            )
        i += 1
    return indices


def ls_path_args(argv: list[str]) -> list[int]:
    """Return ls operands while excluding display and sorting option values."""
    return parse_options(
        argv,
        flags="-a -A -b -B -c -C -d -D -f -F -g -G -h -H -i -k -l -L -m -n -N "
        "-o -p -q -Q -r -R -s -S -t -u -U -v -x -X -Z -1 "
        "--all --almost-all --escape --ignore-backups --directory --dired "
        "--file-type --no-group --human-readable --si --dereference-command-line "
        "--dereference-command-line-symlink-to-dir --inode --kibibytes "
        "--dereference --numeric-uid-gid --literal --hide-control-chars "
        "--show-control-chars --quote-name --reverse --recursive --size "
        "--full-time --author --context --zero",
        values="-I -T -w --block-size --format --hide --ignore --indicator-style "
        "--quoting-style --sort --time --time-style --tabsize --width",
        optional_values="--color --hyperlink --classify",
    ).positionals


def wc_path_args(argv: list[str]) -> list[int]:
    """Return wc files; indirect file lists are outside the supported grammar."""
    return parse_options(
        argv,
        stdin=True,
        flags="-c -m -l -L -w --bytes --chars --lines --max-line-length --words",
        values="--total",
    ).positionals


def diff_path_args(argv: list[str]) -> list[int]:
    """Return diff operands without mistaking formatting values for paths."""
    return parse_options(
        argv,
        stdin=True,
        flags="-q -s -y -p -r -N -a -e -n -i -E -Z -b -w -B -t -T -d -c -u "
        "--brief --report-identical-files --side-by-side --left-column "
        "--suppress-common-lines --show-c-function --recursive --new-file "
        "--unidirectional-new-file --ignore-file-name-case --no-ignore-file-name-case "
        "--text --ed --rcs --ignore-case --ignore-tab-expansion --ignore-trailing-space "
        "--ignore-space-change --ignore-all-space --ignore-blank-lines --expand-tabs "
        "--initial-tab --suppress-blank-empty --minimal --speed-large-files "
        "--strip-trailing-cr",
        values="-C -U -W -F -x -S -I --label --width --show-function-line "
        "--exclude --starting-file --ignore-matching-lines --tabsize --horizon-lines "
        "--old-group-format --new-group-format --unchanged-group-format "
        "--changed-group-format --line-format --old-line-format --new-line-format "
        "--unchanged-line-format",
        optional_values="--context --unified --color",
    ).positionals


def tee_path_args(argv: list[str]) -> list[int]:
    """Return tee outputs, including a literal file named ``-``."""
    return parse_options(
        argv,
        flags="-a -i -p --append --ignore-interrupts",
        optional_values="--output-error",
    ).positionals


def touch_path_args(argv: list[str]) -> list[int]:
    """Return touch targets; reference-file options are not supported."""
    return parse_options(
        argv,
        flags="-a -c -f -h -m --no-create --no-dereference",
        values="-d -t --date --time",
    ).positionals


def mkdir_path_args(argv: list[str]) -> list[int]:
    """Return mkdir targets without interpreting a mode or context as a path."""
    return parse_options(
        argv,
        flags="-p -v -Z --parents --verbose",
        values="-m --mode",
        optional_values="--context",
    ).positionals


def cp_path_args(argv: list[str]) -> list[int]:
    """Return copy sources and destinations, requiring separate target values."""
    return _source_destination_path_args(argv)


def mv_path_args(argv: list[str]) -> list[int]:
    """Return move sources and destinations, requiring separate target values."""
    return _source_destination_path_args(argv)


def _source_destination_path_args(argv: list[str]) -> list[int]:
    parsed = parse_options(
        argv,
        flags="-r -R -T -v --recursive --no-target-directory --verbose --strip-trailing-slashes",
        paths="-t --target-directory",
    )
    if len(parsed.path_values) > 1 or (
        parsed.path_values and parsed.options & {"-T", "--no-target-directory"}
    ):
        raise ValueError("Specify only one destination mode and target directory")
    return sorted(parsed.positionals + parsed.path_values)


def gio_trash_path_args(argv: list[str]) -> list[int]:
    """Return trash operands after the subcommand, honoring ``--``."""
    if not argv or argv[0] != "trash":
        return []
    return [i + 1 for i in parse_options(argv[1:], flags="-f --force").positionals]


def pwd_path_args(argv: list[str]) -> list[int]:
    """Validate pwd options; the resolver guards its implicit working directory."""
    parsed = parse_options(argv, flags="-L -P --logical --physical")
    if parsed.positionals:
        raise ValueError("pwd does not accept path operands")
    return []
