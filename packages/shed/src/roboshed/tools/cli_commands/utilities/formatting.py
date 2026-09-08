"""Agent-facing help and correction text for guarded CLI commands."""

from collections.abc import Sequence
from pathlib import Path

from roboshed.models import ActionVerdict, PermissionRule
from roboz import Ctx

from .cmd_spec import CmdSpec

GUARD_ERR_DENIED = "operation={operation!r} DENIED for location={location!r}"
UNRESOLVED_DYNAMIC_PATH = "<unresolved dynamic path>"


def _framed_cli_output(command_line: str, body: str) -> str:
    """Wrap successful or error CLI text in begin/end markers so multi-step runs stay legible."""
    body = body.rstrip("\n")
    return f"--- begin: {command_line} ---\n{body}\n--- end: {command_line} ---"


def _format_constraints_for_deny(ctx: Ctx) -> str:
    """Format allowed paths and permissions for a guard denial message."""
    base = (ctx.base or Path(".")).resolve()
    return format_cli_constraints(
        base_path=base,
        allow_rules=ctx.allow,
        deny_rules=ctx.deny,
        ask_rules=ctx.ask,
        takes_precedence=ctx.takes_precedence,
        default_verdict=ctx.default_verdict,
        command_specs=getattr(ctx, "command_specs", ()),
    )


def format_cli_commands_help(specs: Sequence[CmdSpec]) -> str:
    """Command list, input shape, and examples (no path permissions)."""
    lines = [
        "Available CLI commands:",
        "",
        'Input format: {"file_commands": [{command, argv, stdin?}, ...], "chain": "pipe"|"and"}',
        "  - file_commands: list of commands; each has command, argv (all subprocess-style args)",
        "  - chain (required): \"pipe\" same as '|' (stdout->stdin between commands) or \"and\" same as '&&' (sequential, no passthrough)",
        '  - Use "pipe" only when the next command consumes stdin (e.g. cat|grep, grep|wc). For independent commands (e.g. find + find, ls + find), use "and".',
        "  - One or more commands per call; no shell syntax (|, ;, &&) in command strings (behavior analogy only; use chain + file_commands).",
        "  - Built-in commands accept a supported option grammar: unknown/abbreviated options and missing values are parse errors.",
        "  - Use -- before dash-prefixed file operands; find roots instead need an explicit ./ prefix. Options may follow operands for other commands.",
        "  - Search with PATTERN path or -e PATTERN path; option values such as --max-count 20 and -g '*.py' are not file operands.",
        "  - Auxiliary file options (-f/--file, --ignore-file, --exclude-from, --files0-from, --reference, find reference predicates) and subprocess preprocessors are unsupported.",
        "  - cp/mv target-directory values must be separate literal tokens: -t DIR or --target-directory DIR. Unmatched path globs are parse errors.",
        "  - Guards check explicit path operands or the implicit working directory. Directory descendants and implicit configuration/ignore files are not individually authorized; this is not a process sandbox.",
        "",
    ]
    hints: dict[str, str] = {
        "find": 'start dirs first in argv, then predicates, e.g. argv=[".", "-name", "*.py"]',
        "rg": 'fast search; respects ignore files by default; if matches are unexpectedly missing, target a specific path or use argv=["-uu","-n","pattern","."]',
        "diff": 'both files in argv, e.g. argv=["a.py", "b.py"]',
        "gio": 'safe delete via trash only, e.g. argv=["trash", "path"]',
        "cp": 'copy paths, e.g. argv=["src.txt","dst.txt"] or argv=["-r","src_dir","dst_dir"]',
        "mv": 'move/rename paths, e.g. argv=["src.txt","dst.txt"] or argv=["-t","dest_dir","a.txt"]',
    }
    for spec in sorted(specs, key=lambda s: s.name):
        parts = [spec.name]
        if spec.allowed_patterns:
            allowed = ", ".join(
                sorted(
                    f"{p.pattern.pattern}@{p.position}"
                    if p.position is not None
                    else p.pattern.pattern
                    for p in spec.allowed_patterns
                )
            )
            parts.append(f"allowed: {allowed}")
        elif spec.forbidden_patterns:
            forbidden = ", ".join(
                sorted(
                    f"{p.pattern.pattern}@{p.position}"
                    if p.position is not None
                    else p.pattern.pattern
                    for p in spec.forbidden_patterns
                )
            )
            parts.append(f"forbidden: {forbidden}")
        if spec.name in hints:
            parts.append(f"({hints[spec.name]})")
        lines.append(f"  {'  '.join(parts)}")
    lines.extend(
        [
            "",
            "Examples:",
            "  # Full help (commands + permissions for this run)",
            '  {"chain": "pipe", "file_commands": [{"command": "help", "argv": []}]}',
            "  # Single commands",
            '  {"chain": "pipe", "file_commands": [{"command": "cat", "argv": ["src/main.py"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "grep", "argv": ["-n", "TODO", "src/"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "def main", "src/"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "rg", "argv": ["-n", "todo|fixme|bug", "src/"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "diff", "argv": ["old.py", "new.py"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "cp", "argv": ["source.txt", "copy.txt"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "mv", "argv": ["old.txt", "archived/old.txt"]}]}',
            '  {"chain": "pipe", "file_commands": [{"command": "gio", "argv": ["trash", "old.txt"]}]}',
            "  # Large file reads in pieces",
            '  {"chain": "pipe", "file_commands": [{"command": "wc", "argv": ["-l", "src/big_file.py"]}]}',
            '  {"file_commands": [{"command": "head", "argv": ["-n", "200", "src/big_file.py"]}, {"command": "tail", "argv": ["-n", "40"]}], "chain": "pipe"}',
            "  # Pipe (cat file | grep pattern)",
            '  {"file_commands": [{"command": "cat", "argv": ["file.txt"]}, {"command": "grep", "argv": ["pattern"]}], "chain": "pipe"}',
            "  # And (mkdir sub && touch sub/file)",
            '  {"file_commands": [{"command": "mkdir", "argv": ["sub"]}, {"command": "touch", "argv": ["sub/file"]}], "chain": "and"}',
            "  # And (independent discovery commands, not a pipeline)",
            '  {"file_commands": [{"command": "find", "argv": [".", "-name", "*.py"]}, {"command": "find", "argv": [".", "-type", "d"]}], "chain": "and"}',
        ]
    )
    return "\n".join(lines)


def _resolved_rule_pattern(rule: PermissionRule) -> str:
    p = rule.pattern
    if p is None:
        raise TypeError("PermissionRule pattern must not be None")
    if isinstance(p, str):
        return p
    try:
        resolved = p()
    except Exception:
        return UNRESOLVED_DYNAMIC_PATH
    return resolved or UNRESOLVED_DYNAMIC_PATH


def _rule_bullets(rules: list[PermissionRule]) -> list[str]:
    return [
        f"- {_resolved_rule_pattern(r)} → {', '.join(sorted(op.name for op in r.operations))}"
        for r in rules
    ] or ["- (none)"]


def _tool_desc(cmd: str, spec: CmdSpec) -> str | None:
    if spec.hint:
        return spec.hint
    if spec.requires_ask:
        return "Requires user confirmation (yes/no)."
    if spec.allowed_patterns:
        return "Allowed argv patterns: " + ", ".join(
            sorted(
                f"{p.pattern.pattern}@{p.position}"
                if p.position is not None
                else p.pattern.pattern
                for p in spec.allowed_patterns
            )
        )
    if spec.forbidden_patterns:
        return "Forbidden argv patterns: " + ", ".join(
            sorted(
                f"{p.pattern.pattern}@{p.position}"
                if p.position is not None
                else p.pattern.pattern
                for p in spec.forbidden_patterns
            )
        )
    return None


def format_cli_constraints(
    *,
    base_path: Path,
    allow_rules: list[PermissionRule],
    deny_rules: list[PermissionRule],
    ask_rules: list[PermissionRule],
    takes_precedence: ActionVerdict,
    default_verdict: ActionVerdict,
    command_specs: Sequence[CmdSpec],
) -> str:
    """Produce explicit constraints document for agents (path rules + optional tool hints)."""
    specs = command_specs
    base = base_path.resolve()

    precedence = "allow" if takes_precedence == ActionVerdict.allow else "deny"

    blocks = [
        "CLI constraints (what you can and cannot do):",
        "",
        "## Scope",
        "",
        f"- Working directory (shorthand for relative paths): {base}",
        "- Tool path inputs may be relative (resolved under this directory) or absolute.",
        "- Permission-rule patterns may be relative to this directory or absolute filesystem paths.",
        "- Glob semantics: *, ?, and [seq] match within one path segment; ** matches zero or more segments; dir/** also matches dir itself.",
        "- Authorization is only from allow/deny/ask rules below (including default when nothing matches).",
        "",
        "## Allowed operations",
        "",
        *_rule_bullets(allow_rules),
        "",
        "## Denied operations",
        "",
        *_rule_bullets(deny_rules),
        "",
        f"Precedence: when both allow and deny match, {precedence} wins.",
        f"Default when no rule matches: {'deny' if default_verdict == ActionVerdict.deny else 'allow'}.",
        "",
        "## Overwrite requires DELETE permission",
        "",
        "Writing to an existing file (tee, etc.) counts as overwrite. "
        "Overwrite requires both CREATE and DELETE. CREATE-only: new files only.",
        "",
    ]
    if ask_rules:
        blocks.extend(
            ["## User confirmation required", "", *_rule_bullets(ask_rules), ""]
        )
    if specs:
        tool_items = [
            (spec.name, desc)
            for spec in sorted(specs, key=lambda s: s.name)
            if (desc := _tool_desc(spec.name, spec))
        ]
        tool_lines = [f"- **{cmd}**: {desc}" for cmd, desc in tool_items] or [
            "- (none)"
        ]
        blocks.extend(["## Tool-specific behaviors", "", *tool_lines])
    return "\n".join(blocks)


def format_cli_full_help(
    specs: Sequence[CmdSpec],
    base_path: Path,
    allow_rules: list[PermissionRule],
    deny_rules: list[PermissionRule],
    ask_rules: list[PermissionRule],
    takes_precedence: ActionVerdict,
    default_verdict: ActionVerdict,
) -> str:
    """Commands + input shape + examples, then path permissions (scope, rules, tool hints)."""
    return (
        format_cli_commands_help(specs)
        + "\n\n"
        + format_cli_constraints(
            base_path=base_path,
            allow_rules=allow_rules,
            deny_rules=deny_rules,
            ask_rules=ask_rules,
            takes_precedence=takes_precedence,
            default_verdict=default_verdict,
            command_specs=specs,
        )
    )


def cli_help_message(specs: Sequence[CmdSpec], ctx: Ctx) -> str:
    """Full help: commands section plus constraints for this tool chain."""
    return format_cli_full_help(
        specs,
        base_path=ctx.base,
        allow_rules=ctx.allow_rules,
        deny_rules=ctx.deny_rules,
        ask_rules=ctx.ask_rules,
        takes_precedence=ctx.takes_precedence,
        default_verdict=ctx.default_verdict,
    )
