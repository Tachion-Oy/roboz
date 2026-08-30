"""Tests for the three-tier project workspace policy."""

from pathlib import Path
from unittest.mock import patch

import pytest
from roboz.runtime import EventPipe

from roboz.standard.models import ActionVerdict, Operation
from roboz.standard.skills.cli_commands.runtime.utils import (
    check_allow_deny_permission,
    check_ask_permission,
)
from roboz.standard.sandbox import Project

CREATE = Operation.CREATE
DELETE = Operation.DELETE
READ = Operation.READ

# Two concrete name sets; the second proves behavior tracks the supplied names
# rather than any baked-in convention.
LAYOUT = {
    "readonly_dirname": "readonly",
    "workspace_dirname": "workspace",
    "projects_dirname": "projects",
    "safe_scripts_dirname": "safe-scripts",
}
CUSTOM_LAYOUT = {
    "readonly_dirname": "ro",
    "workspace_dirname": "ws",
    "projects_dirname": "proj",
    "safe_scripts_dirname": "scripts",
}


def _project(
    root: Path,
    slug: str = "alice",
    layout: dict[str, str] | None = None,
    subdirs: dict[str, str] | None = None,
) -> Project:
    selected_layout = LAYOUT if layout is None else layout
    return Project(
        workspace_root=root,
        slug=slug,
        subdirs={} if subdirs is None else subdirs,
        **selected_layout,
    )


def _perms(project: Project):
    return project.workspace_permissions()


def _verdict(perms, location: Path, op: Operation) -> ActionVerdict:
    return check_allow_deny_permission(
        location=location,
        op_type=op,
        takes_precedence=perms["takes_precedence"],
        allow_rules=perms["allow_rules"],
        deny_rules=perms["deny_rules"],
        default_verdict=perms["default_verdict"],
        base_path=perms["base"],
    )


def test_ensure_creates_folders_named_by_the_supplied_names(tmp_path: Path) -> None:
    project = _project(tmp_path / "root", layout=CUSTOM_LAYOUT)
    project.ensure()
    root = project.base_dir
    assert (root / "ro").is_dir()
    assert (root / "ws").is_dir()
    assert (root / "proj").is_dir()
    assert (root / "proj" / "alice").is_dir()
    assert not (root / "proj" / "bob").exists()


def test_scripts_dir_is_under_the_readonly_tier(tmp_path: Path) -> None:
    project = _project(tmp_path, layout=CUSTOM_LAYOUT)
    project.ensure()
    project.scripts_dir.mkdir(parents=True)
    # The safe-scripts folder really lives at <readonly>/<safe_scripts> on disk.
    assert project.scripts_dir == tmp_path / "ro" / "scripts"
    assert project.scripts_dir.is_dir()
    assert project.scripts_dir.parent == project.readonly_dir


def test_validate_passes_on_clean_or_partial_root(tmp_path: Path) -> None:
    project = _project(tmp_path / "root")
    # Non-existent root is treated as clean.
    project.validate()
    # Only tier folders present (and a loose file) is fine.
    project.ensure()
    (project.base_dir / "stray_file.txt").write_text("ok", encoding="utf-8")
    project.validate()


def test_validate_rejects_unexpected_top_level_folder(tmp_path: Path) -> None:
    project = _project(tmp_path / "root")
    project.ensure()
    (project.base_dir / "sneaky").mkdir()
    with pytest.raises(ValueError, match="Unexpected folders.*sneaky"):
        project.validate()


def test_reads_allowed_everywhere_inside_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    perms = _perms(project)
    for location in (
        project.readonly_dir / "ref.txt",
        project.workspace_dir / "scratch.txt",
        project.project_dir("alice") / "mine.txt",
        project.project_dir("bob") / "theirs.txt",
        project.base_dir / "bare.txt",
    ):
        assert _verdict(perms, location, READ) == ActionVerdict.allow


def test_reads_outside_root_are_denied(tmp_path: Path) -> None:
    project = _project(tmp_path / "root")
    perms = _perms(project)
    outside = tmp_path / "elsewhere" / "secret.txt"
    assert _verdict(perms, outside, READ) == ActionVerdict.deny


def test_symlink_resolving_outside_root_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    project = _project(root)
    project.ensure()
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "data.txt").write_text("classified", encoding="utf-8")
    escape = project.root / "escape"
    escape.symlink_to(secret_dir)
    perms = _perms(project)
    # The symlink lives under the agent's own project, but resolves outside root.
    assert _verdict(perms, escape / "data.txt", READ) == ActionVerdict.deny
    assert _verdict(perms, escape / "data.txt", CREATE) == ActionVerdict.deny


@pytest.mark.parametrize("op", [CREATE, DELETE])
def test_own_project_writes_allowed_without_prompt(
    tmp_path: Path, op: Operation
) -> None:
    project = _project(tmp_path)
    perms = _perms(project)
    location = project.root / "draft.md"
    location.write_text("draft", encoding="utf-8")
    assert _verdict(perms, location, op) == ActionVerdict.allow
    verdict, outcome = check_ask_permission(
        location=location,
        op_type=op,
        ask_rules=perms["ask_rules"],
        base_path=perms["base"],
    )
    assert verdict == ActionVerdict.allow
    assert outcome == "no_prompt"


@pytest.mark.parametrize("op", [CREATE, DELETE])
def test_other_project_and_readonly_writes_denied(
    tmp_path: Path, op: Operation
) -> None:
    project = _project(tmp_path)
    perms = _perms(project)
    for location in (
        project.project_dir("bob") / "x.md",
        project.readonly_dir / "x.md",
        project.base_dir / "bare.md",
    ):
        assert _verdict(perms, location, op) == ActionVerdict.deny


@pytest.mark.parametrize("op", [CREATE, DELETE])
def test_shared_workspace_writes_prompt(tmp_path: Path, op: Operation) -> None:
    project = _project(tmp_path)
    perms = project.workspace_permissions()
    pipe = EventPipe()
    location = project.workspace_dir / "shared.md"
    location.write_text("shared", encoding="utf-8")
    # Policy allows it, then the ask rule forces a prompt.
    assert _verdict(perms, location, op) == ActionVerdict.allow
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="yes"
    ) as prompt:
        verdict, outcome = check_ask_permission(
            location=location,
            op_type=op,
            ask_rules=perms["ask_rules"],
            base_path=perms["base"],
            pipe=pipe,
        )
        assert verdict == ActionVerdict.allow
        assert outcome == "user_confirmed"
        prompt.assert_called_once()
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="no"
    ):
        verdict, outcome = check_ask_permission(
            location=location,
            op_type=op,
            ask_rules=perms["ask_rules"],
            base_path=perms["base"],
            pipe=pipe,
        )
        assert verdict == ActionVerdict.deny
        assert outcome == "user_declined"


def test_project_owner_keys_the_writable_folder(tmp_path: Path) -> None:
    alice_project = _project(tmp_path, slug="alice")
    bob_project = _project(tmp_path, slug="bob")
    alice = _perms(alice_project)
    bob = _perms(bob_project)
    shared_target = alice_project.root / "f.md"
    # Same folder: writable for its owner, denied for the other agent.
    assert _verdict(alice, shared_target, CREATE) == ActionVerdict.allow
    assert _verdict(bob, shared_target, CREATE) == ActionVerdict.deny


def test_custom_names_drive_the_permission_behavior(tmp_path: Path) -> None:
    # Build with custom tier names and verify behavior at the real custom-named
    # folders - no glob-string equality.
    project = _project(tmp_path, layout=CUSTOM_LAYOUT)
    perms = project.workspace_permissions()
    pipe = EventPipe()

    own = project.root / "f"  # <root>/proj/alice/f
    own.write_text("mine", encoding="utf-8")
    assert _verdict(perms, own, CREATE) == ActionVerdict.allow
    _, own_outcome = check_ask_permission(
        location=own,
        op_type=CREATE,
        ask_rules=perms["ask_rules"],
        base_path=perms["base"],
    )
    assert own_outcome == "no_prompt"

    readonly_target = project.readonly_dir / "f"  # <root>/ro/f
    assert _verdict(perms, readonly_target, CREATE) == ActionVerdict.deny

    shared = project.workspace_dir / "f"  # <root>/ws/f
    shared.write_text("shared", encoding="utf-8")
    assert _verdict(perms, shared, CREATE) == ActionVerdict.allow
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="yes"
    ) as prompt:
        shared_verdict, shared_outcome = check_ask_permission(
            location=shared,
            op_type=CREATE,
            ask_rules=perms["ask_rules"],
            base_path=perms["base"],
            pipe=pipe,
        )
        assert shared_verdict == ActionVerdict.allow
        assert shared_outcome == "user_confirmed"
        prompt.assert_called_once()


def test_project_resolves_caller_named_subfolders_as_attributes() -> None:
    # The standard library bakes in no folder names: arbitrary, caller-supplied subdirs resolve as
    # attributes under the project root. Custom folder names also prove the root tracks
    # the supplied tier names, not a baked-in convention.
    project = _project(
        Path("/hub"),
        slug="my-project",
        layout=CUSTOM_LAYOUT,
        subdirs={"logs": "conv", "memory": "mem", "user_goals": "goals"},
    )

    assert project.base_dir == Path("/hub")
    assert project.root == project.project_dir("my-project")
    assert project.logs == project.root / "conv"
    assert project.memory == project.root / "mem"
    # An unfamiliar folder works the same because the behavior is pure configuration.
    assert project.user_goals == project.root / "goals"
    with pytest.raises(AttributeError):
        _ = project.nonexistent


def test_project_builds_its_workspace_permissions(tmp_path: Path) -> None:
    project = _project(tmp_path / "root")

    permissions = project.workspace_permissions()

    assert project.root.is_dir()
    assert permissions["base"] == project.base_dir


def test_project_root_is_a_no_prompt_write_location(tmp_path: Path) -> None:
    # The project root must be exactly the no-prompt project dir, so an
    # agent owning the project writes there without prompting.
    project = _project(tmp_path / "hub", subdirs={"memory": "memory"})
    perms = _perms(project)
    target = project.memory / "note.md"
    assert _verdict(perms, target, CREATE) == ActionVerdict.allow
