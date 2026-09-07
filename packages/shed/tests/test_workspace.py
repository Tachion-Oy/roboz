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
