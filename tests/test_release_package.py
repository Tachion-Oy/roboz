import pytest

from scripts import release_package

from scripts.release_package import resolve_tag


@pytest.mark.parametrize(
    "name",
    tuple(release_package.PROJECTS),
)
def test_release_selects_one_matching_package(name):
    tag = f"{name}-v{release_package.project_version(name)}"
    assert resolve_tag(tag) == name


@pytest.mark.parametrize(
    "tag", ["v0.1.0", "other-v0.1.0", "roboz-v9.0.0", "roboshed-v0.1.0"]
)
def test_release_rejects_unknown_packages_and_version_mismatches(tag):
    with pytest.raises(ValueError):
        resolve_tag(tag)


def test_release_selects_verified_bytes_without_rebuilding(tmp_path, monkeypatch):
    from scripts import release_package

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nversion = "0.1.1"\n')
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    artifacts = ["roboz-0.1.1-py3-none-any.whl", "roboz-0.1.1.tar.gz"]
    for filename in artifacts:
        (candidates / filename).write_bytes(b"verified bytes: " + filename.encode())
    (candidates / "roboshed-0.1.0a1-py3-none-any.whl").write_bytes(b"other package")
    monkeypatch.setattr(release_package, "ROOT", project)
    monkeypatch.setattr(release_package, "PROJECTS", {"roboz": project})
    monkeypatch.setattr(
        "sys.argv",
        ["release_package.py", "roboz-v0.1.1", "--from-dist", str(candidates)],
    )

    release_package.main()

    selected = project / "dist/release"
    assert sorted(path.name for path in selected.iterdir()) == sorted(artifacts)
    for filename in artifacts:
        assert (selected / filename).read_bytes() == (
            candidates / filename
        ).read_bytes()
    # A subsequent run cannot overwrite a reviewed or user-owned output.
    with pytest.raises(SystemExit):
        release_package.main()


def test_release_rejects_incomplete_verified_artifacts(tmp_path, monkeypatch):
    from scripts import release_package

    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.1"\n')
    (tmp_path / "roboz-0.1.1-py3-none-any.whl").write_bytes(b"wheel only")
    monkeypatch.setattr(release_package, "ROOT", tmp_path)
    monkeypatch.setattr(release_package, "PROJECTS", {"roboz": tmp_path})
    monkeypatch.setattr(
        "sys.argv", ["release_package.py", "roboz-v0.1.1", "--from-dist", str(tmp_path)]
    )
    with pytest.raises(ValueError, match="Missing wheel or source"):
        release_package.main()
    assert not (tmp_path / "dist/release").exists()


def test_check_emits_selection_without_building(tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    name = "roboz"
    tag = f"{name}-v{release_package.project_version(name)}"
    monkeypatch.setattr(
        "sys.argv",
        ["release_package.py", tag, "--check", "--github-output", str(output)],
    )
    release_package.main()
    assert (
        output.read_text()
        == f"package={name}\nversion={release_package.project_version(name)}\n"
    )
