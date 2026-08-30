"""Resolve CLI-skill file-command inputs before permission checks."""

from collections.abc import Sequence
from pathlib import Path

from roboz import factory
from roboz.models import Message, Severity, Truncation

from roboz.standard.models.core import (
    CommandReady,
    GuardFileSingle,
    Help,
    ParseError,
)
from roboz.standard.sandbox import Operation
from roboz.standard.skills.cli_commands.inputs import RunFileCommands
from roboz.standard.skills.cli_commands.utilities.cmd_spec import CmdSpec
from roboz.standard.skills.cli_commands.utilities.constants import (
    ERR_ARGS_NOT_ALLOWED,
    ERR_COMMAND_NOT_ALLOWED,
    ERR_FORBIDDEN_PATTERN,
    ERR_HINT_USE_HELP,
    ERR_MISSING_WHITELISTED_SUBCOMMAND,
    ERR_SOURCE_DESTINATION_PATHS,
    GLOB_CHARS,
)
from roboz.standard.skills.cli_commands.utilities.formatting import cli_help_message
from roboz.standard.skills.cli_commands.utilities.path_extractors import resolve_path_indices
from roboz.standard.skills.cli_commands.runtime.types import (
    ResolvedFileCommand,
    RunFileCommandsCtx,
)
from roboz.standard.skills.cli_commands.runtime.utils import resolve_path_token


def _specs_by_name(specs: Sequence[CmdSpec]) -> dict[str, CmdSpec]:
    return {spec.name: spec for spec in specs}


def _validate_input(
    input: RunFileCommands, specs: Sequence[CmdSpec]
) -> ParseError | None:
    """Return a parse error or None if valid."""
    cli_command = input.file_commands[0]
    cmd = cli_command.command.strip().lower()
    specs_by_name = _specs_by_name(specs)

    if cmd not in specs_by_name:
        return ParseError(
            message=ERR_COMMAND_NOT_ALLOWED.format(cmd=cmd) + ERR_HINT_USE_HELP,
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )

    spec = specs_by_name[cmd]
    path_indices = set(
        resolve_path_indices(list(cli_command.argv), spec.path_extractor)
    )
    args_error = _validate_args_for_spec(list(cli_command.argv), spec, path_indices)
    if args_error:
        return ParseError(
            message=args_error,
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )
    if spec.source_operation is not None and len(path_indices) < 2:
        return ParseError(
            message=ERR_SOURCE_DESTINATION_PATHS,
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )
    return None


def _validate_args_for_spec(
    argv: list[str], spec: CmdSpec, path_indices: set[int]
) -> str | None:
    """Return an error message when non-path argv tokens violate spec rules."""
    non_path_indices = [i for i in range(len(argv)) if i not in path_indices]

    if spec.allowed_patterns:
        global_patterns = []
        for rule in spec.allowed_patterns:
            if rule.position is None:
                global_patterns.append(rule.pattern)
                continue

            pos = rule.position
            if pos < 0 or pos >= len(argv) or pos in path_indices:
                return ERR_MISSING_WHITELISTED_SUBCOMMAND
            if not rule.pattern.fullmatch(argv[pos]):
                return ERR_ARGS_NOT_ALLOWED.format(arg=argv[pos])

        if global_patterns:
            for idx in non_path_indices:
                tok = argv[idx]
                if not any(p.fullmatch(tok) for p in global_patterns):
                    return ERR_ARGS_NOT_ALLOWED.format(arg=tok)
        return None

    if spec.forbidden_patterns:
        for rule in spec.forbidden_patterns:
            if rule.position is None:
                indices = non_path_indices
            else:
                pos = rule.position
                if pos < 0 or pos >= len(argv) or pos in path_indices:
                    continue
                indices = [pos]

            for idx in indices:
                tok = argv[idx]
                if rule.pattern.fullmatch(tok):
                    return ERR_FORBIDDEN_PATTERN.format(tok=tok)
    return None


def _resolve_paths_and_argv(
    input: RunFileCommands, base: Path, spec: CmdSpec
) -> tuple[list[Path], list[str], list[int], list[list[Path]]]:
    """Resolve guarded locations and argv, including token-index path grouping."""
    raw_argv = list(input.file_commands[0].argv)
    path_indices = resolve_path_indices(raw_argv, spec.path_extractor)
    path_groups = _resolve_path_groups(raw_argv, path_indices, base)
    return (
        _guard_locations(path_indices, path_groups, base),
        _argv_with_resolved_paths(raw_argv, path_indices, path_groups),
        path_indices,
        path_groups,
    )


def _resolve_path_groups(
    raw_argv: list[str], path_indices: list[int], base: Path
) -> list[list[Path]]:
    """Resolve each argv path operand, preserving one group per original token."""
    path_tokens = [raw_argv[i] for i in path_indices]
    return _resolve_path_groups_under_base(path_tokens, base)


def _resolve_path_groups_under_base(
    paths: Sequence[str], base: Path
) -> list[list[Path]]:
    """Resolve each path token to one or more paths under base."""
    groups: list[list[Path]] = []
    resolved_base = base.resolve()
    if not paths:
        return [[resolved_base]]
    for path_token in paths:
        raw_path = Path(path_token)
        if any(char in path_token for char in GLOB_CHARS):
            groups.append(
                _expand_path_glob(
                    raw_path,
                    path_token,
                    None if raw_path.is_absolute() else resolved_base,
                )
            )
        else:
            groups.append([resolve_path_token(path_token, base=resolved_base)])
    return groups


def _expand_path_glob(
    raw_path: Path, path_token: str, resolved_base: Path | None
) -> list[Path]:
    """Expand a CLI path operand against files that already exist.

    Permission rules are matched later against concrete paths and do not use this
    expansion. This only mirrors normal shell-style argv behavior for commands
    such as ``cat src/**/*.py``: existing matches replace the glob token before
    subprocess execution and before per-file guard checks.

    ``Path.glob`` takes a pattern relative to the directory it is called on.
    The branch below makes the two modes explicit: relative paths use the tool
    base; absolute paths do not.
    """
    if raw_path.is_absolute():
        if resolved_base is not None:
            raise ValueError("resolved_base must be None for absolute glob paths")
        anchor, glob_pattern = _absolute_glob_anchor_and_pattern(raw_path)
    else:
        if resolved_base is None:
            raise ValueError("resolved_base is required for relative glob paths")
        anchor, glob_pattern = resolved_base, path_token
    return sorted(path.resolve() for path in anchor.glob(glob_pattern))


def _absolute_glob_anchor_and_pattern(raw_path: Path) -> tuple[Path, str]:
    """For ``/repo/src/**/*.py``, call ``Path("/repo/src").glob("**/*.py")``."""
    glob_index = _first_glob_part_index(raw_path)
    if glob_index is None:
        raise ValueError(f"Path has no glob segment: {raw_path}")
    return (
        Path(*raw_path.parts[:glob_index]),
        Path(*raw_path.parts[glob_index:]).as_posix(),
    )


def _first_glob_part_index(path: Path) -> int | None:
    return next(
        (
            i
            for i, part in enumerate(path.parts)
            if any(char in part for char in GLOB_CHARS)
        ),
        None,
    )


def _guard_locations(
    path_indices: list[int], path_groups: list[list[Path]], base: Path
) -> list[Path]:
    """Return concrete filesystem locations that need permission checks."""
    if not path_indices:
        return [base.resolve()]
    return [path for group in path_groups for path in group]


def _argv_with_resolved_paths(
    raw_argv: list[str], path_indices: list[int], path_groups: list[list[Path]]
) -> list[str]:
    """Replace path operands in argv with resolved paths, expanding globs in place."""
    if not path_indices:
        return raw_argv

    group_by_index = {
        idx: [str(p) for p in path_groups[group_i]]
        for group_i, idx in enumerate(path_indices)
    }
    resolved_argv: list[str] = []
    for i, tok in enumerate(raw_argv):
        if i not in group_by_index:
            resolved_argv.append(tok)
            continue
        resolved_argv.extend(group_by_index[i])
    return resolved_argv


def _get_help(input: RunFileCommands, ctx: RunFileCommandsCtx) -> Help | None:
    cli_command = input.file_commands[0]
    if cli_command.command.strip().lower() == "help" and not cli_command.argv:
        return Help(
            message=cli_help_message(ctx.specs, ctx),
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )
    return None


def _ready_value(
    *, command: str, argv: list[str], base: Path, stdin: str | None
) -> CommandReady:
    executable_argv = [command, *argv]
    return CommandReady.model_validate(
        {
            "command_name": command,
            "argv": executable_argv,
            "base_workdir": base,
            "display_command": " ".join(executable_argv),
            "stdin": stdin,
        }
    )


def _guard_items(
    input: RunFileCommands, base: Path, spec: CmdSpec
) -> list[GuardFileSingle]:
    resolved_paths, resolved_argv, path_indices, path_groups = _resolve_paths_and_argv(
        input, base, spec
    )
    ready_command = input.file_commands[0].model_copy(update={"argv": resolved_argv})
    value = _ready_value(
        command=spec.name,
        argv=ready_command.argv,
        base=base,
        stdin=ready_command.stdin,
    )
    if spec.source_operation is not None:
        return _relocation_guard_items(
            raw_argv=list(input.file_commands[0].argv),
            path_indices=path_indices,
            path_groups=path_groups,
            source_operation=spec.source_operation,
            destination_operation=spec.operation,
            value=value,
        )
    return [
        GuardFileSingle(operation=spec.operation, location=path, value=value)
        for path in resolved_paths
    ]


def _relocation_guard_items(
    *,
    raw_argv: list[str],
    path_indices: list[int],
    path_groups: list[list[Path]],
    source_operation: Operation,
    destination_operation: Operation,
    value: CommandReady,
) -> list[GuardFileSingle]:
    """Emit source_operation for sources and destination_operation for destinations."""
    source_indices, destination_indices = _move_source_destination_indices(
        raw_argv, path_indices
    )
    group_by_index = {idx: path_groups[i] for i, idx in enumerate(path_indices)}
    source_paths = [
        path for idx in source_indices for path in group_by_index.get(idx, [])
    ]
    destination_tokens = [
        path for idx in destination_indices for path in group_by_index.get(idx, [])
    ]
    if not source_paths or not destination_tokens:
        return []

    target_dir_mode = _move_target_directory_index(raw_argv) is not None
    items: list[GuardFileSingle] = []
    for destination_token in destination_tokens:
        for source, destination in _move_destinations(
            source_paths=source_paths,
            destination_token=destination_token,
            target_dir_mode=target_dir_mode,
        ):
            items.append(
                GuardFileSingle(
                    operation=source_operation, location=source, value=value
                )
            )
            items.append(
                GuardFileSingle(
                    operation=destination_operation, location=destination, value=value
                )
            )
    return items


def _move_source_destination_indices(
    raw_argv: list[str], path_indices: list[int]
) -> tuple[list[int], list[int]]:
    target_dir_index = _move_target_directory_index(raw_argv)
    if target_dir_index in path_indices:
        return [idx for idx in path_indices if idx != target_dir_index], [
            target_dir_index
        ]
    if len(path_indices) < 2:
        return [], []
    return path_indices[:-1], [path_indices[-1]]


def _move_target_directory_index(raw_argv: list[str]) -> int | None:
    for i, tok in enumerate(raw_argv[:-1]):
        if tok in {"-t", "--target-directory"}:
            return i + 1
    return None


def _move_destinations(
    *, source_paths: list[Path], destination_token: Path, target_dir_mode: bool
) -> list[tuple[Path, Path]]:
    if not source_paths:
        return []
    if target_dir_mode or destination_token.is_dir():
        return [(source, destination_token / source.name) for source in source_paths]
    if len(source_paths) == 1:
        return [(source_paths[0], destination_token)]
    # Invalid shape (multiple sources, non-directory destination) fails in
    # subprocess; guard still models write intent per source.
    return [(source, destination_token) for source in source_paths]


@factory
def resolve_input(
    input: RunFileCommands, messages: list[Message], ctx: RunFileCommandsCtx
) -> ResolvedFileCommand | Help | ParseError:
    """Resolve to one internal file-command envelope."""
    specs = ctx.specs
    base = ctx.base

    help_result = _get_help(input, ctx)
    if help_result is not None:
        return help_result

    invalid_input = _validate_input(input, specs)
    if invalid_input:
        invalid_input.message = (
            f"{invalid_input.message}\n\n{cli_help_message(specs, ctx)}"
        )
        return invalid_input

    spec = _specs_by_name(specs)[input.file_commands[0].command.strip().lower()]
    return ResolvedFileCommand(
        original_input=input, items=_guard_items(input, base, spec)
    )
