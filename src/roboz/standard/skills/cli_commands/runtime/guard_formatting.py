"""Formatting helpers for guarded command operations."""

from pathlib import Path

from roboz.standard.sandbox import ActionVerdict
from roboz.standard.skills.cli_commands.runtime.types import GuardCtx
from roboz.standard.skills.cli_commands.utilities.formatting import _rule_bullets


def format_guard_constraints(ctx: GuardCtx) -> str:
    """Format path and operation constraints for a guard denial."""
    base = (ctx.base or Path(".")).resolve()
    precedence = "allow" if ctx.takes_precedence == ActionVerdict.allow else "deny"
    default = ctx.default_verdict

    blocks = [
        "Tool constraints (what you can and cannot do):",
        "",
        "## Scope",
        "",
        f"- Working directory: {base}",
        "- Permission-rule patterns may be relative to this directory or absolute filesystem paths.",
        "- Authorization is only from allow/deny/ask rules below, including the default when nothing matches.",
        "",
        "## Allowed operations",
        "",
        *_rule_bullets(ctx.allow),
        "",
        "## Denied operations",
        "",
        *_rule_bullets(ctx.deny),
        "",
        f"Precedence: when both allow and deny match, {precedence} wins.",
        f"Default when no rule matches: {'deny' if default == ActionVerdict.deny else 'allow'}.",
        "",
        "## Overwrite requires DELETE permission",
        "",
        "Writing to an existing file counts as overwrite. Overwrite requires both CREATE and DELETE.",
    ]
    if ctx.ask:
        blocks.extend(
            ["", "## User confirmation required", "", *_rule_bullets(ctx.ask)]
        )
    return "\n".join(blocks)
