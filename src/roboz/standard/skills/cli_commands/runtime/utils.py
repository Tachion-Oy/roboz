"""Path and permission utilities for guarded file operations."""

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from roboz.runtime import EventPipe
from roboz.runtime import interact_with_user

from roboz.standard.sandbox import ActionVerdict, Operation, PermissionRule
from roboz.standard.skills.cli_commands.utilities.constants import GLOB_CHARS

AskPermissionOutcome = Literal["no_prompt", "user_confirmed", "user_declined"]

def resolve_path_token(raw_path: str, *, base: Path) -> Path:
    """Resolve a single raw path token relative to ``base`` unless absolute."""
    path = Path(raw_path)
    resolved_base = base.resolve()
    return path.resolve() if path.is_absolute() else (resolved_base / raw_path).resolve()


def resolve_single_file_path(
    raw_path: str, *, base: Path, label: str = "Path"
) -> Path:
    """Resolve one non-glob file path token for structured tools."""
    if any(c in raw_path for c in GLOB_CHARS):
        raise ValueError(f"{label} must name one file, not a glob: {raw_path}")
    resolved = resolve_path_token(raw_path, base=base)
    if resolved.name in {"", ".", ".."}:
        raise ValueError(f"{label} must refer to a regular file")
    return resolved


def check_allow_deny_permission(
    location: Path,
    op_type: Operation,
    takes_precedence: ActionVerdict,
    allow_rules: list[PermissionRule],
    deny_rules: list[PermissionRule],
    default_verdict: ActionVerdict,
    base_path: Path | None = None,
) -> ActionVerdict:
    """Check allow and deny rules based on precedence order.

    Args:
        location: The file path to validate.
        op_type: The type of operation being performed.
        takes_precedence: Which verdict type takes precedence when both match.
        allow_rules: List of allow permission rules.
        deny_rules: List of deny permission rules.
        default_verdict: Verdict when no allow or deny rule matches.
        base_path: If set, patterns match paths relative to this base.

    Returns:
        The resulting verdict (allow or deny).
    """
    allow_match = check_rule(location, op_type, allow_rules, base_path=base_path)
    deny_match = check_rule(location, op_type, deny_rules, base_path=base_path)

    if allow_match and deny_match:
        return takes_precedence
    if allow_match and not deny_match:
        return ActionVerdict.allow
    if deny_match and not allow_match:
        return ActionVerdict.deny

    return default_verdict


def check_ask_permission(
    location: Path,
    op_type: Operation,
    ask_rules: list[PermissionRule],
    base_path: Path | None = None,
    pipe: EventPipe | None = None,
) -> tuple[ActionVerdict, AskPermissionOutcome]:
    """Check if operation requires user confirmation.

    If the operation matches an ask rule, prompts via runtime output context.
    When no rule matches, returns allow and ``pipe`` is ignored.

    Args:
        location: The file path to validate.
        op_type: The type of operation being performed.
        ask_rules: List of ask permission rules.
        base_path: If set, match patterns against path relative to this base.
        pipe: Required when an ask rule matches; used as an execution context guard.

    Returns:
        Tuple of:
        - ActionVerdict.allow if user confirms, ActionVerdict.deny otherwise.
        - AskPermissionOutcome explaining ask-rule prompt result.
    """

    if not check_rule(location, op_type, ask_rules, base_path=base_path):
        return ActionVerdict.allow, "no_prompt"

    if pipe is None:
        raise ValueError("Output missing: no way to ask user")
    message_to_user = f"Allow {op_type} for the location {location}? (yes/no)"
    try:
        reply = interact_with_user(message_to_user, with_reply=True)
    except RuntimeError as exc:
        # Keep a stable guard error for callers/tests that rely on this contract.
        raise ValueError("Output missing: no way to ask user") from exc

    if reply is not None and reply.strip().lower().startswith("y"):
        return ActionVerdict.allow, "user_confirmed"

    return ActionVerdict.deny, "user_declined"


def check_rule(
    location: Path,
    op_type: Operation,
    rules: list[PermissionRule],
    base_path: Path | None = None,
) -> bool:
    """Check if an operation type matches any rule in the list.

    Uses path_matcher predicate for pattern matching.

    Args:
        location: The file path to validate.
        op_type: The type of operation being performed.
        rules: List of permission rules to check against.
        base_path: Required when any applicable rule has a relative pattern.
                   Ignored for rules whose pattern is absolute.

    Returns:
        True if the operation matches any rule, False otherwise.
    """
    _require_absolute_base_path(base_path)

    for rule in rules:
        if op_type not in rule.operations:
            continue
        resolved_pattern = (
            rule.pattern if isinstance(rule.pattern, str) else rule.pattern()
        )
        effective_base = _effective_base_for_pattern(resolved_pattern, base_path)
        if path_matcher(resolved_pattern, base_path=effective_base)(location):
            return True
    return False


def _effective_base_for_pattern(pattern: str, base_path: Path | None) -> Path | None:
    """Resolve the base to use when matching ``pattern``.

    Absolute patterns (starting with ``/``) always match against the full
    filesystem path, so the base is irrelevant and dropped.  Relative patterns
    require a base to be meaningful; a missing base is a caller error.
    """
    if pattern.strip().startswith("/"):
        return None
    if base_path is None:
        raise ValueError(f"Relative pattern {pattern.strip()!r} requires a base_path")
    return base_path


def _require_absolute_base_path(base_path: Path | None) -> None:
    if base_path is not None and not base_path.is_absolute():
        raise ValueError("base_path must be absolute")


def resolve_tool_base(base: Path | None) -> Path:
    if base is None:
        raise ValueError("Base is required")
    if not base.is_absolute():
        raise ValueError("Base must be absolute")
    resolved_base = base.resolve()
    if resolved_base.is_file():
        raise ValueError("Base needs to be a folder")
    return resolved_base


def _normalize_glob_path(value: str) -> str:
    """Normalize paths and patterns to POSIX separators for stable matching."""
    normalized = value.replace("\\", "/").strip()
    normalized = re.sub(r"/+", "/", normalized)
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def _glob_match(path: str, pattern: str) -> bool:
    normalized_path = _normalize_glob_path(path)
    stripped_pattern = _normalize_glob_path(pattern)
    if not stripped_pattern:
        return False
    if stripped_pattern.endswith("/**") and normalized_path == stripped_pattern[:-3]:
        return True
    return PurePosixPath(normalized_path).full_match(stripped_pattern)


def _match_one(path: Path, base_path: Path | None, pattern: str) -> bool:
    stripped_pattern = pattern.strip()

    if base_path is None:
        return _glob_match(path.as_posix(), stripped_pattern)

    try:
        relative = path.resolve().relative_to(base_path.resolve())
    except ValueError:
        return False

    return _glob_match(relative.as_posix(), stripped_pattern)


def path_matcher(
    patterns: str | list[str] | None,
    base_path: Path | None = None,
) -> Callable[[Path], bool]:
    """Create a predicate that tests if a path matches any pattern.

    Args:
        patterns: Glob pattern(s) to match. If None, matches everything.
                  ``*`` and ``?`` match within one path segment; ``**`` matches
                  zero or more path segments.
        base_path: If provided, match against relative path from this base.
                   Paths outside the base do not match.
                   If None (default), match against full path string.

    Returns:
        Predicate function that returns True if path matches any pattern.

    Examples:
        # Match files in test directory
        matches_test_path = path_matcher("test/*")
        test_files = [p for p in paths if matches_test_path(p)]

        # Relative path matching
        matches_generated_path = path_matcher(["node_modules/*", ".venv/**"], base_path=project_root)
        generated = [p for p in paths if matches_generated_path(p)]
    """
    _require_absolute_base_path(base_path)

    if patterns is None:
        return lambda p: True

    if isinstance(patterns, str):
        patterns = [patterns]

    return lambda p: any(_match_one(p, base_path, pat) for pat in patterns)
