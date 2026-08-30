"""Tests for guarded file-operation utilities."""

from pathlib import Path

import pytest

from roboz.standard.models import ActionVerdict, Operation, PermissionRule
from roboz.standard.skills.cli_commands.runtime.utils import (
    _effective_base_for_pattern,
    check_allow_deny_permission,
    path_matcher,
)

def test_path_matcher_matches_single_segment_pattern(tmp_path: Path):
    """A single-segment glob only matches one path segment."""
    matches_python_file = path_matcher("*.py", tmp_path)

    assert matches_python_file(tmp_path / "test.py") is True
    assert matches_python_file(tmp_path / "test" / "main.py") is False
    assert matches_python_file(tmp_path / "README.md") is False
    assert matches_python_file(tmp_path / "test" / "script.sh") is False


def test_path_matcher_matches_any_configured_pattern(tmp_path: Path):
    """Multiple patterns are ORed together."""
    matches_source_path = path_matcher(["*.py", "*.js", "*.ts", "test/*"], tmp_path)

    assert matches_source_path(tmp_path / "app.py") is True
    assert matches_source_path(tmp_path / "index.js") is True
    assert matches_source_path(tmp_path / "test" / "app.tsx") is True
    assert matches_source_path(tmp_path / "README.md") is False
    assert matches_source_path(tmp_path / "styles.css") is False


def test_path_matcher_matches_relative_patterns_under_base():
    """Relative patterns match paths under base."""
    base = Path("/home/user/project")

    matches_relative_pattern = path_matcher(
        ["node_modules/*", ".venv/**", "**/*.pyc", "*/test/*"], base
    )

    # Matches relative path patterns
    assert matches_relative_pattern(base / "node_modules" / "package.json") is True
    assert matches_relative_pattern(base / "src" / "app.pyc") is True
    assert matches_relative_pattern(base / ".venv" / "bin" / "python") is True
    assert matches_relative_pattern(base / "__pycache__" / "test" / "test.py") is True

    # Does not match paths outside those patterns
    assert (
        matches_relative_pattern(base / "__pycache__" / "test" / "test1" / "test.py")
        is False
    )
    assert matches_relative_pattern(base / "src" / "main.py") is False
    assert matches_relative_pattern(base / "README.md") is False


def test_path_matcher_requires_absolute_base_path():
    with pytest.raises(ValueError, match="base_path must be absolute"):
        path_matcher("*.py", Path("relative/base"))


def test_permission_check_requires_absolute_base_path_before_rules():
    with pytest.raises(ValueError, match="base_path must be absolute"):
        check_allow_deny_permission(
            location=Path("file.txt"),
            op_type=Operation.READ,
            takes_precedence=ActionVerdict.deny,
            allow_rules=[],
            deny_rules=[],
            default_verdict=ActionVerdict.deny,
            base_path=Path("relative/base"),
        )


def test_effective_base_drops_base_for_absolute_pattern():
    """_effective_base_for_pattern returns None for absolute patterns, ignoring the base."""
    base = Path("/home/user/project")
    assert _effective_base_for_pattern("/tmp/other/*.md", base) is None
    assert _effective_base_for_pattern("/etc/passwd", base) is None


def test_effective_base_raises_for_relative_pattern_without_base():
    """_effective_base_for_pattern rejects relative pattern with no base_path."""
    with pytest.raises(ValueError, match="requires a base_path"):
        _effective_base_for_pattern("roboz_data/**", base_path=None)


def test_effective_base_returns_base_for_relative_pattern():
    """_effective_base_for_pattern passes through the base for relative patterns."""
    base = Path("/home/user/project")
    assert _effective_base_for_pattern("src/**", base) is base


def test_ancestor_glob_rule_already_covers_intermediate_descendants(
    tmp_path: Path,
) -> None:
    """With fnmatch-based matching, parent `/**` covers nested descendants."""
    location = tmp_path / "roboz_data" / "code_task_plan_generator"
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="roboz_data/**", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="**", operations={Operation.CREATE})
    ]

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.allow


def test_recursive_subtree_rule_matches_root_and_nested_paths(tmp_path: Path) -> None:
    matches_worktree_subtree = path_matcher("worktrees/**", tmp_path)

    assert matches_worktree_subtree(tmp_path / "worktrees") is True
    assert matches_worktree_subtree(tmp_path / "worktrees" / "project") is True
    assert (
        matches_worktree_subtree(
            tmp_path / "worktrees" / "project" / "nested" / "file.txt"
        )
        is True
    )
    assert matches_worktree_subtree(tmp_path / "other" / "worktrees") is False


def test_star_matches_one_path_segment_only(tmp_path: Path) -> None:
    matches_one_level_under_worktrees = path_matcher("worktrees/*", tmp_path)

    assert matches_one_level_under_worktrees(tmp_path / "worktrees" / "project") is True
    assert (
        matches_one_level_under_worktrees(tmp_path / "worktrees" / "project" / "nested")
        is False
    )


def test_allow_only_rules_can_use_explicit_default_deny(tmp_path: Path) -> None:
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="worktrees/**", operations={Operation.CREATE})
    ]

    assert (
        check_allow_deny_permission(
            location=tmp_path / "worktrees" / "project" / "nested",
            op_type=Operation.CREATE,
            takes_precedence=ActionVerdict.allow,
            allow_rules=allow_rules,
            deny_rules=[],
            base_path=tmp_path,
            default_verdict=ActionVerdict.deny,
        )
        == ActionVerdict.allow
    )
    assert (
        check_allow_deny_permission(
            location=tmp_path / "outside" / "nested",
            op_type=Operation.CREATE,
            takes_precedence=ActionVerdict.allow,
            allow_rules=allow_rules,
            deny_rules=[],
            base_path=tmp_path,
            default_verdict=ActionVerdict.deny,
        )
        == ActionVerdict.deny
    )


def test_absolute_pattern_without_base_matches_full_path() -> None:
    """Absolute rule patterns match against the full filesystem path when no base is set."""
    location = Path("/etc/passwd")
    matches = path_matcher("/etc/passwd")
    assert matches(location) is True


def test_absolute_glob_pattern_without_base_matches_subtree() -> None:
    """Absolute glob patterns match the full path tree without a base."""
    matches = path_matcher("/etc/**")
    assert matches(Path("/etc/passwd")) is True
    assert matches(Path("/etc/ssl/certs/ca.pem")) is True
    assert matches(Path("/var/log/syslog")) is False


def test_check_rule_absolute_deny_fires_even_with_tool_base(tmp_path: Path) -> None:
    """An absolute deny rule matches its target regardless of the tool's base path.

    Previously, a path outside the base silently fell through (relative_to raised),
    so an absolute deny rule on e.g. /etc/** could never fire when a base was set.
    After the fix, the absolute pattern is routed to full-path matching and the rule
    fires correctly independent of the base.
    """
    base = tmp_path / "workspace"
    base.mkdir()
    protected = Path("/etc/passwd")
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="/etc/**", operations={Operation.READ})
    ]
    result = check_allow_deny_permission(
        location=protected,
        op_type=Operation.READ,
        takes_precedence=ActionVerdict.allow,
        allow_rules=[],
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=base,
    )
    assert result == ActionVerdict.deny


def test_check_rule_absolute_allow_rule_with_base_applies_to_outside_path(
    tmp_path: Path,
) -> None:
    """An absolute allow rule matches a path outside the base when base is also set."""
    base = tmp_path / "workspace"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("content")
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern=str(outside.resolve()), operations={Operation.READ})
    ]
    result = check_allow_deny_permission(
        location=outside,
        op_type=Operation.READ,
        takes_precedence=ActionVerdict.deny,
        allow_rules=allow_rules,
        deny_rules=[],
        default_verdict=ActionVerdict.deny,
        base_path=base,
    )
    assert result == ActionVerdict.allow
