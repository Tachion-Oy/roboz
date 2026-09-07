from dataclasses import replace
from pathlib import Path

import pytest
from roboshed.workspace import Project, Workspace


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
