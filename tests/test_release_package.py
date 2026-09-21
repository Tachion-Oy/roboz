import subprocess

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
    (candidates / "another-0.1.0-py3-none-any.whl").write_bytes(b"other package")
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


@pytest.fixture
def release_repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    projects = {
        name: root / path.relative_to(release_package.ROOT)
        for name, path in release_package.PROJECTS.items()
    }
    monkeypatch.setattr(release_package, "ROOT", root)
    monkeypatch.setattr(release_package, "PROJECTS", projects)

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    for name, path in projects.items():
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        )
    git("add", ".")
    git("commit", "-m", "Initial package metadata")
    return git


@pytest.mark.parametrize("name", sorted(release_package.PUBLISHABLE_PROJECTS))
@pytest.mark.parametrize("annotated", [False, True])
def test_candidate_resolves_existing_package_tags(release_repo, name, annotated):
    tag = f"{name}-v0.1.0"
    args = ("-a", "-m", "Candidate") if annotated else ()
    release_repo("tag", *args, tag)
    commit = release_repo("rev-parse", "HEAD")

    assert release_package.resolve_candidate(tag, commit) == (name, "0.1.0", commit)


def test_candidate_reads_old_tag_metadata_without_changing_checkout(release_repo):
    tag = "roboz-v0.1.0"
    release_repo("tag", tag)
    candidate = release_repo("rev-parse", "HEAD")
    metadata = release_package.PROJECTS["roboz"] / "pyproject.toml"
    metadata.write_text('[project]\nname = "roboz"\nversion = "0.2.0"\n')
    release_repo("commit", "-am", "Prepare the next version")
    current_main = release_repo("rev-parse", "HEAD")

    assert release_package.resolve_candidate(tag, current_main) == (
        "roboz",
        "0.1.0",
        candidate,
    )
    assert release_repo("rev-parse", "HEAD") == current_main
    assert release_repo("status", "--porcelain") == ""
    assert release_package.project_version("roboz") == "0.2.0"


@pytest.mark.parametrize(
    "ref", ["main", "HEAD", "refs/tags/roboz-v0.1.0", "other-v0.1.0"]
)
def test_candidate_rejects_non_package_tag_inputs(release_repo, ref):
    with pytest.raises(ValueError, match="Expected roboz"):
        release_package.resolve_candidate(ref, release_repo("rev-parse", "HEAD"))


def test_candidate_rejects_a_raw_commit(release_repo):
    commit = release_repo("rev-parse", "HEAD")
    with pytest.raises(ValueError, match="Expected roboz"):
        release_package.resolve_candidate(commit, commit)


@pytest.mark.parametrize("create_branch", [False, True])
def test_candidate_requires_a_tag_even_if_a_same_named_branch_exists(
    release_repo, create_branch
):
    tag = "roboz-v0.1.0"
    if create_branch:
        release_repo("branch", tag)
    with pytest.raises(ValueError, match="existing commit"):
        release_package.resolve_candidate(tag, release_repo("rev-parse", "HEAD"))


def test_candidate_rejects_unmerged_tag(release_repo):
    main = release_repo("rev-parse", "HEAD")
    release_repo("switch", "-c", "unmerged")
    release_repo("commit", "--allow-empty", "-m", "Unreviewed work")
    release_repo("tag", "roboz-v0.1.0")
    release_repo("switch", "main")
    with pytest.raises(ValueError, match="main's history"):
        release_package.resolve_candidate("roboz-v0.1.0", main)


def test_candidate_uses_main_at_invocation_even_if_main_advances(release_repo):
    main_at_invocation = release_repo("rev-parse", "HEAD")
    release_repo("commit", "--allow-empty", "-m", "Later work on main")
    release_repo("tag", "roboz-v0.1.0")
    with pytest.raises(ValueError, match="main's history"):
        release_package.resolve_candidate("roboz-v0.1.0", main_at_invocation)


def test_candidate_rejects_a_tagged_non_commit_object(release_repo):
    tree = release_repo("rev-parse", "HEAD^{tree}")
    release_repo("tag", "roboz-v0.1.0", tree)
    with pytest.raises(ValueError, match="existing commit"):
        release_package.resolve_candidate(
            "roboz-v0.1.0", release_repo("rev-parse", "HEAD")
        )


def test_candidate_rejects_version_mismatch(release_repo):
    release_repo("tag", "roboz-v9.0.0")
    with pytest.raises(ValueError, match="does not match tagged"):
        release_package.resolve_candidate(
            "roboz-v9.0.0", release_repo("rev-parse", "HEAD")
        )


def test_candidate_rejects_wrong_package_metadata(release_repo):
    metadata = release_package.PROJECTS["roboz"] / "pyproject.toml"
    metadata.write_text('[project]\nname = "another"\nversion = "0.1.0"\n')
    release_repo("commit", "-am", "Incorrect project name")
    release_repo("tag", "roboz-v0.1.0")
    with pytest.raises(ValueError, match="does not name 'roboz'"):
        release_package.resolve_candidate(
            "roboz-v0.1.0", release_repo("rev-parse", "HEAD")
        )


def test_candidate_requires_immutable_main_commit(release_repo):
    release_repo("tag", "roboz-v0.1.0")
    with pytest.raises(ValueError, match="full commit SHA"):
        release_package.resolve_candidate("roboz-v0.1.0", "main")


def test_resolve_emits_tagged_version_and_commit_without_building(
    release_repo, tmp_path, monkeypatch
):
    output = tmp_path / "outputs"
    release_repo("tag", "roboz-v0.1.0")
    commit = release_repo("rev-parse", "HEAD")
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_package.py",
            "roboz-v0.1.0",
            "--resolve",
            "--base-commit",
            commit,
            "--github-output",
            str(output),
        ],
    )

    release_package.main()

    assert output.read_text() == f"package=roboz\nversion=0.1.0\nsha={commit}\n"
    assert not (release_package.ROOT / "dist").exists()


@pytest.mark.parametrize(
    "args", [["--resolve"], ["--check", "--base-commit", "a" * 40]]
)
def test_resolve_requires_base_commit_only_in_resolution_mode(args, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_package.py", "roboz-v0.1.0", *args])
    with pytest.raises(SystemExit) as error:
        release_package.main()
    assert error.value.code == 2
