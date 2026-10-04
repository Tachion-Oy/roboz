"""Regular-file and stdin preparation shared by read commands."""

import re
import shlex
import stat
from pathlib import Path

from roboz.shed.models import CommandReady, Operation

from ..contracts import ParsedCommand, PreparedCommand, TokenRule
from ..paths import expand_source_path, resolve_literal_path
from ..tokens import END_OPTIONS, PATH_TOKEN


_READ_TOKENS = (
    END_OPTIONS,
    PATH_TOKEN,
    TokenRule(tag="ARG", pattern=re.compile(r"-")),
)

def prepare_read(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Require READ on regular files, preserving repeated operands and stdin."""
    first_operand = parsed.operands[0] if parsed.operands else len(parsed.tokens)
    argv = parsed.argv[:first_operand]
    operations: list[tuple[Operation, Path]] = []
    for index in parsed.operands:
        value, tag = parsed.tokens[index]
        if (value, tag) == ("-", "ARG"):
            argv.append(value)
            continue
        if tag != "PTH":
            raise ValueError("Reader operands must be PTH or stdin '-' tagged ARG")
        for path_arg in expand_source_path(value, base):
            resolved_path = resolve_literal_path(path_arg, base)
            file_stat = Path(path_arg).stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"Reader path must be an existing regular file: {resolved_path}"
                )
            if file_stat.st_nlink > 1:
                raise ValueError(f"Hard-linked files are unsupported: {resolved_path}")
            argv.append(path_arg)
            operations.append((Operation.READ, resolved_path))
    return PreparedCommand(
        ready=CommandReady(
            command_name=argv[0],
            argv=argv,
            base_workdir=base,
            stdin="",
            display_command=shlex.join(argv),
        ),
        operations=list(dict.fromkeys(operations)),
    )


def prepare_counted_read(parsed: ParsedCommand, base: Path) -> PreparedCommand:
    """Check head/tail counts before preparing their regular-file operands."""
    for option in ("-n", "-c"):
        if option in parsed.options:
            value = parsed.tokens[parsed.options[option] + 1][0]
            if re.fullmatch(r"[0-9]+", value) is None:
                raise ValueError(
                    f"{option} requires an unsigned decimal count tagged ARG"
                )
    return prepare_read(parsed, base)
