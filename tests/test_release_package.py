import pytest
from datetime import date

from scripts import release_package

from scripts.release_package import resolve_tag


@pytest.mark.parametrize(
    "tag,name",
    [
        ("roboz-v0.1.1", "roboz"),
        ("roboz-shed-v0.1.0a1", "roboz-shed"),
        ("roboz-openai-v0.1.0a1", "roboz-openai"),
        ("roboz-proton-bridge-v0.1.0b1", "roboz-proton-bridge"),
    ],
)
def test_release_selects_one_matching_package(tag, name):
    assert resolve_tag(tag) == name


@pytest.mark.parametrize(
    "tag", ["v0.1.0", "other-v0.1.0", "roboz-v9.0.0", "roboz-shed-v0.1.0"]
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
    (candidates / "roboz_shed-0.1.0a1-py3-none-any.whl").write_bytes(b"other package")
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
    with pytest.raises(SystemExit):
        release_package.main()
    assert not (tmp_path / "dist/release").exists()


@pytest.fixture
def prepared_project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## 1.2.3 - 2026-01-02\n\n- Fix a bug.\n"
    )
    monkeypatch.setattr(release_package, "PROJECTS", {"roboz": tmp_path})
    return tmp_path


def test_preparation_accepts_dated_release_without_modifying_files(prepared_project):
    changelog = prepared_project / "CHANGELOG.md"
    before = changelog.read_bytes()
    release_package.check_preparation("roboz", today=date(2026, 1, 2))
    assert changelog.read_bytes() == before


@pytest.mark.parametrize(
    "changelog, message",
    [
        ("## 1.2.3 - 2026-01-02\n- Fix.\n", "Unreleased"),
        (
            "## Unreleased\n- Pending change.\n## 1.2.3 - 2026-01-02\n- Fix.\n",
            "move Unreleased",
        ),
        ("## Unreleased\n## 1.2.2 - 2026-01-02\n- Fix.\n", "expected"),
        ("## Unreleased\n## 1.2.3\n- Fix.\n", "YYYY-MM-DD"),
        ("## Unreleased\n## 1.2.3 - 2026-02-30\n- Fix.\n", "day"),
        ("## Unreleased\n## 1.2.3 - 2026-01-03\n- Fix.\n", "future"),
        (
            "## Unreleased\n## 1.2.3 - 2026-01-02\n### Fixed\n<!-- todo -->\n",
            "nonempty",
        ),
        (
            "## Unreleased\n## 1.2.3 - 2026-01-02\n- Fix.\n## 1.2.3\nOld notes.\n",
            "duplicate",
        ),
    ],
)
def test_preparation_rejects_incomplete_release(prepared_project, changelog, message):
    (prepared_project / "CHANGELOG.md").write_text(changelog)
    with pytest.raises(ValueError, match=message):
        release_package.check_preparation("roboz", today=date(2026, 1, 2))


def test_manual_preparation_emits_same_selection_as_tag(prepared_project, monkeypatch):
    output = prepared_project / "outputs"
    for selection in (["--package", "roboz"], ["roboz-v1.2.3"]):
        monkeypatch.setattr(
            "sys.argv",
            [
                "release_package.py",
                *selection,
                "--check",
                "--github-output",
                str(output),
            ],
        )
        release_package.main()
    result = "package=roboz\nversion=1.2.3\ntag=roboz-v1.2.3\n"
    assert output.read_text() == result * 2
    assert not (prepared_project / "dist").exists()


def test_rejects_version_that_could_inject_github_outputs(prepared_project):
    (prepared_project / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3\\npackage=other"\n'
    )
    with pytest.raises(ValueError, match="Invalid release version"):
        release_package.project_version("roboz")
