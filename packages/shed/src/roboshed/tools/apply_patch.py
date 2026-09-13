"""Single-file apply_patch connector: guarded path checks then Python string replace."""

from pathlib import Path

from roboshed.identifiers import APPLY_PATCH_TOOL_NAME, FILE_EDITING_SKILL_NAME
from roboshed.models import (
    ActionVerdict,
    ApplyPatch,
    ApplyPatchReady,
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    Operation,
    ParseError,
    PermissionRule,
)
from roboshed.tools.cli_commands.utilities.formatting import _framed_cli_output
from roboshed.tools.guard import build_guarded_tool_chain
from roboshed.tools.truncation import default_cli_truncation
from roboshed.tools.types import ResolvedFileCommand
from roboshed.tools.utils import resolve_single_file_path, resolve_tool_base
from roboshed.tools.contexts import GuardContext
from roboz.models import Message, Str
from roboz.models.truncation import Severity, Truncation, TruncationSpec
from roboz.runtime.pipe import EventPipe
from roboz.tooling import Tool
from roboz.tooling.decorators import factory

APPLY_PATCH_NAME: str = APPLY_PATCH_TOOL_NAME


@factory
def apply_patch(
    input: ApplyPatch, messages: list[Message], ctx: Path
) -> ResolvedFileCommand | ParseError:
    """Prepare an exact single-file string replacement for permission checking."""
    base = ctx.resolve()
    try:
        location = resolve_single_file_path(input.path, base=base)
        if location.exists() and not location.is_file():
            raise ValueError("Path must refer to a regular file")
    except ValueError as e:
        return ParseError(
            message=str(e),
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )

    return ResolvedFileCommand(
        original_input=input,
        items=[
            GuardFileSingle(
                operation=Operation.CREATE,
                location=location,
                value=ApplyPatchReady(
                    path=input.path,
                    old_string=input.old_string,
                    new_string=input.new_string,
                    replace_all=input.replace_all,
                ),
            )
        ],
    )


def _perform_replace(
    file_path: Path, old: str, new: str, replace_all: bool
) -> tuple[bool, str]:
    if not old:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new, encoding="utf-8")
        except OSError as e:
            return False, f"apply_patch: could not write file: {e}"
        return True, "[success] via full-file rewrite."

    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"apply_patch: could not read file: {e}"

    match_count = text.count(old)
    if match_count == 0:
        return False, "apply_patch: old_string not found"

    if not replace_all and match_count > 1:
        return (
            False,
            f"apply_patch: old_string matched {match_count} times; use replace_all=true to replace all, "
            "or include more context so the match is unique",
        )

    updated_text = text.replace(old, new)
    try:
        file_path.write_text(updated_text, encoding="utf-8")
    except OSError as e:
        return False, f"apply_patch: could not write file: {e}"

    return True, f"[success] Replaced {match_count} occurrence(s)."


@factory
def execute_apply_patch_replace(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: TruncationSpec,
) -> Str:
    """Apply a permitted exact string replacement to one file."""
    truncation = ctx
    if input.status != GuardStatus.ALLOWED:
        msg = input.message or f"apply_patch: unexpected guard status {input.status}"
        body = _framed_cli_output("apply_patch string-replace", msg)
        return Str(value=body.strip(), truncation=truncation)
    payload = input.items[0].value
    if not isinstance(payload, ApplyPatchReady):
        msg = "apply_patch: internal error: expected ApplyPatchReady payload"
        body = _framed_cli_output("apply_patch string-replace", msg)
        return Str(value=body.strip(), truncation=truncation)

    command_line = f"apply_patch string-replace {payload.path}"
    abs_path = input.items[0].location
    ok, out = _perform_replace(
        abs_path, payload.old_string, payload.new_string, payload.replace_all
    )
    if not ok:
        body = _framed_cli_output(command_line, out)
        return Str(value=body.strip(), truncation=truncation)

    body = _framed_cli_output(command_line, out)
    return Str(value=body.strip(), truncation=truncation)


def get_apply_patch(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict | None = None,
    execute_cli_truncation: TruncationSpec = default_cli_truncation(),
    pipe: EventPipe | None = None,
    file_editing_skill_name: str = FILE_EDITING_SKILL_NAME,
) -> list[Tool]:
    """Single-file apply_patch: same guard chain as CLI tools, Python replace executor.

    Args:
        base: Absolute directory against which relative paths are resolved.
        default_verdict: Decision used when no permission rule matches.
        deny_rules: Rules that reject matching filesystem operations.
        allow_rules: Rules that permit matching filesystem operations.
        ask_rules: Rules that require interactive approval when matched.
        takes_precedence: Verdict that wins when allow and deny rules both match.
        execute_cli_truncation: Truncation policy for executor output.
        pipe: Optional event pipe used for interactive guard prompts.
        file_editing_skill_name: Skill id (``Skill.name``) referenced in the tool
            description as the source of detailed file-editing usage instructions.
            Defaults to ``FILE_EDITING_SKILL_NAME``.
    """
    allow = list(allow_rules if allow_rules else [])
    deny = list(deny_rules if deny_rules else [])
    ask = list(ask_rules if ask_rules else [])
    precedence = takes_precedence if takes_precedence else ActionVerdict.deny
    base = resolve_tool_base(base)

    guard_ctx = GuardContext(
        base=base,
        takes_precedence=precedence,
        allow=allow,
        deny=deny,
        ask=ask,
        default_verdict=default_verdict,
        pipe=pipe,
    )

    description = (
        "Perform a file edit to one file using literal find/replace under path guards. "
        f"The possibly available `{file_editing_skill_name}` skill provides detailed "
        "instructions for editing workflow and best practices. "
        "Input is `{path, old_string, new_string, replace_all}`. "
        "Input `path` may be relative to the tool base or absolute; no globs. "
        "Permission rules used by the guard may be either relative patterns or absolute filesystem patterns. "
        "Matching is exact substring (not regex). "
        "If `replace_all` is false, `old_string` must appear exactly once; if true, every occurrence is "
        "replaced and at least one match is required. "
        "If `old_string` is empty, the tool rewrites the whole target file with `new_string`; "
        "if the file is missing, it is created."
    )

    entry = apply_patch(base).copy(name=APPLY_PATCH_NAME, description=description)
    return build_guarded_tool_chain(
        entry=entry,
        guard_ctx=guard_ctx,
        execute=execute_apply_patch_replace(execute_cli_truncation),
    )
