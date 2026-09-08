from dataclasses import replace
from pathlib import Path

import pytest
from roboshed.workspace import Project, Workspace, WorkspacePermissions
from roboshed.models import ActionVerdict, Operation
from roboshed.tools.utils import check_allow_deny_permission

from roboz import Ctx


def test_workspace_names_and_persistence_are_independently_configurable(tmp_path):
    workspace = Workspace(
        tmp_path / "root", readonly="references", shared="team", projects="jobs"
    )
    project = Project(
        workspace,
        "research",
        logs_dir=tmp_path / "separate-logs",
        snapshots_dir=Path("summaries"),
        memory_dir=Path("knowledge"),
    )
    assert project.root == tmp_path / "root/jobs/research"
    assert project.logs == tmp_path / "separate-logs"
    assert project.snapshots == project.root / "summaries"
    assert project.artifact_dir("reports/draft") == project.root / "reports/draft"
    assert workspace.shared_dir == tmp_path / "root/team"
    assert not workspace.root.exists()
    with pytest.raises(ValueError, match="inside"):
        project.artifact_dir("../escape")
    with pytest.raises(ValueError, match="overlap"):
        replace(project, memory_dir=Path("summaries/nested"))
    with pytest.raises(ValueError, match="overlap"):
        Workspace(tmp_path, shared="readonly/nested")


@pytest.mark.parametrize("field", ["logs_dir", "snapshots_dir", "memory_dir"])
def test_project_accepts_nested_relative_persistence_without_creating_dirs(
    tmp_path: Path, field: str
):
    workspace = Workspace(tmp_path / "workspace")
    project = Project(workspace, "research", **{field: Path("storage/nested")})
    assert getattr(project, field.removesuffix("_dir")) == (
        project.root / "storage/nested"
    )
    assert not workspace.root.exists()


@pytest.mark.parametrize("field", ["logs_dir", "snapshots_dir", "memory_dir"])
@pytest.mark.parametrize("path", ["../escape", "nested/../../escape", "."])
def test_project_rejects_relative_persistence_outside_project(
    tmp_path: Path, field: str, path: str
):
    workspace = Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError, match="inside"):
        Project(workspace, "research", **{field: Path(path)})
    assert not workspace.root.exists()


@pytest.mark.parametrize("field", ["logs_dir", "snapshots_dir", "memory_dir"])
def test_project_rejects_escaping_persistence_symlink(tmp_path: Path, field: str):
    workspace = Workspace(tmp_path / "workspace")
    project_root = workspace.project_dir("research")
    project_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        Project(workspace, "research", **{field: Path("linked/nested")})
    assert not (outside / "nested").exists()


@pytest.mark.parametrize("field", ["logs_dir", "snapshots_dir", "memory_dir"])
def test_project_accepts_absolute_external_persistence(tmp_path: Path, field: str):
    workspace = Workspace(tmp_path / "workspace")
    outside = tmp_path / "outside"
    project = Project(workspace, "research", **{field: outside / "nested/../storage"})
    assert getattr(project, field.removesuffix("_dir")) == outside / "storage"
    assert not workspace.root.exists()
    assert not outside.exists()


@pytest.mark.parametrize("field", ["logs_dir", "snapshots_dir", "memory_dir"])
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_project_rejects_overlapping_persistence(
    tmp_path: Path, field: str, absolute: bool, nested: bool
):
    project = Project(Workspace(tmp_path / "workspace"), "research")
    other = "memory" if field == "logs_dir" else "logs"
    path = Path(other) / "nested" if nested else Path(other)
    if absolute:
        path = project.root / path
    with pytest.raises(ValueError, match="overlap"):
        replace(project, **{field: path})
    assert not project.workspace.root.exists()


@pytest.mark.parametrize("project_scoped", [False, True])
def test_workspace_denies_escape_and_symlink_target(tmp_path: Path, project_scoped):
    from roboshed.models import ActionVerdict, Operation
    from roboshed.tools.guard import resolve_allow_verdict

    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    project = Project(Workspace(root), "research")
    workspace = project.permissions if project_scoped else WorkspacePermissions.local(root)
    ctx = Ctx(
        base=workspace.base,
        takes_precedence=workspace.takes_precedence,
        allow=list(workspace.allow),
        deny=[],
        ask=[],
        default_verdict=workspace.default_verdict,
    )
    for path in [outside, root / ".." / "outside.txt", root / "link.txt"]:
        assert resolve_allow_verdict(path, Operation.READ, ctx)[0] == ActionVerdict.deny
    assert (
        resolve_allow_verdict((project.root if project_scoped else root) / "new.txt", Operation.CREATE, ctx)[0]
        == ActionVerdict.allow
    )


@pytest.mark.parametrize(
    "area,operation,allowed",
    [
        ("references", Operation.READ, True),
        ("team", Operation.READ, True),
        ("work/other", Operation.READ, True),
        ("work/my-project", Operation.CREATE, True),
        ("work/my-project", Operation.DELETE, True),
        ("references", Operation.CREATE, False),
        ("work/other", Operation.DELETE, False),
        ("../outside", Operation.READ, False),
        ("../outside", Operation.CREATE, False),
    ],
)
def test_configured_workspace_permission_boundaries(tmp_path, area, operation, allowed):
    project = Project(
        Workspace(tmp_path / "root", readonly="references", shared="team", projects="work"),
        "my-project",
    )
    policy = project.permissions
    verdict = check_allow_deny_permission(
        location=policy.base / area / "file.txt",
        op_type=operation,
        takes_precedence=policy.takes_precedence,
        allow_rules=policy.allow,
        deny_rules=policy.deny,
        default_verdict=policy.default_verdict,
        base_path=policy.base,
    )
    assert verdict == (ActionVerdict.allow if allowed else ActionVerdict.deny)


@pytest.mark.parametrize(
    "reply,expected", [("yes", ActionVerdict.allow), ("no", ActionVerdict.deny)]
)
def test_shared_writes_require_confirmation(tmp_path, monkeypatch, reply, expected):
    from roboshed.tools import utils
    from roboz.runtime import EventPipe

    project = Project(Workspace(tmp_path / "root"), "my-project")
    policy = project.permissions
    prompts = []

    def interact(message, *, with_reply):
        assert with_reply
        prompts.append(message)
        return reply

    monkeypatch.setattr(utils, "interact_with_user", interact)
    verdict, _ = utils.check_ask_permission(
        location=project.workspace.shared_dir / "file.txt",
        op_type=Operation.CREATE,
        ask_rules=policy.ask,
        base_path=policy.base,
        pipe=EventPipe(),
    )
    assert verdict == expected and len(prompts) == 1


@pytest.mark.parametrize(
    "projects,shared,slug,sibling",
    [
        ("projects", "shared", "*", "projects/other"),
        ("projects", "shared", "**", "projects/other/nested"),
        ("projects", "team?", "mine", "team1"),
        ("work[ab]", "shared", "mine", "worka/mine"),
    ],
)
def test_project_permission_paths_treat_globs_as_literal_names(
    tmp_path, projects, shared, slug, sibling
):
    from roboshed.tools.utils import check_rule

    project = Project(Workspace(tmp_path, projects=projects, shared=shared), slug)
    policy = project.permissions
    for operation in (Operation.CREATE, Operation.DELETE):
        for location in (project.root, project.root / "file.txt"):
            assert check_rule(location, operation, list(policy.allow), policy.base)
            assert not check_rule(location, operation, list(policy.ask), policy.base)
        for location in (project.workspace.shared_dir, project.workspace.shared_dir / "file.txt"):
            assert check_rule(location, operation, list(policy.allow), policy.base)
            assert check_rule(location, operation, list(policy.ask), policy.base)
        assert not check_rule(tmp_path / sibling / "file.txt", operation, list(policy.allow), policy.base)
