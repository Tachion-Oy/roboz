from dataclasses import replace
from pathlib import Path

import pytest
from roboshed.workspace import Project, Workspace, WorkspacePermissions

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


def test_workspace_denies_escape_and_symlink_target(tmp_path: Path):
    from roboshed.models import ActionVerdict, Operation
    from roboshed.tools.guard import resolve_allow_verdict

    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    workspace = WorkspacePermissions.local(root)
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
        resolve_allow_verdict(root / "new.txt", Operation.CREATE, ctx)[0]
        == ActionVerdict.allow
    )
