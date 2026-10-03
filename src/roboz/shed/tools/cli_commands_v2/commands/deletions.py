"""No-follow entry inspection and DELETE requirements shared by rm and gio trash."""

import shlex
import stat
from pathlib import Path
from time import monotonic

from roboz.shed.models import CommandReady, Operation
from roboz.shed.tools.cli_commands.utilities.constants import SUBPROCESS_TIMEOUT_SECONDS

from ..contracts import PreparedCommand
from ..paths import directory_entries, expand_source_path, inspect_entry_path


def prepare_deletion(
    argv: list[str], operands: list[str], base: Path, *, recursive: bool
) -> PreparedCommand:
    """Guard all selected entries before one native invocation, retaining argv text."""
    deadline = monotonic() + SUBPROCESS_TIMEOUT_SECONDS

    def check_deadline() -> None:
        if monotonic() >= deadline:
            raise ValueError(
                f"Deletion preparation timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds"
            )

    operations: list[tuple[Operation, Path]] = []
    seen: set[Path] = set()
    for operand in operands:
        for path_arg in expand_source_path(
            operand, base, allow_terminal_symlinks=True, check_deadline=check_deadline
        ):
            argv.append(path_arg)
            pending = [path_arg]
            while pending:
                check_deadline()
                current = pending.pop()
                location, entry = inspect_entry_path(current, base)
                # Inspect each argument even if its canonical location was seen:
                # suffixes can change whether the native command reaches an entry.
                if location in seen:
                    continue
                operations.append((Operation.DELETE, location))
                if entry is None:
                    continue
                seen.add(location)
                if recursive and stat.S_ISDIR(entry.st_mode):
                    children = [
                        entry.path
                        for entry in directory_entries(Path(current), check_deadline)
                    ]
                    children.sort(reverse=True)
                    check_deadline()
                    pending.extend(children)
    check_deadline()
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
