import hashlib
import io
import json
import subprocess
from urllib.error import HTTPError

import pytest

from scripts import check_published_package as published
from scripts import release_package


@pytest.fixture
def candidates(tmp_path):
    directory = tmp_path / "candidates"
    directory.mkdir()
    for filename in published.filenames("roboz", "1.2.3"):
        (directory / filename).write_bytes(filename.encode())
    return directory


@pytest.fixture
def index_server(monkeypatch):
    """Replace only HTTP and elapsed time; exercise real index handling."""
    responses = {}
    requests = []
    now = [0.0]

    def open_url(url, timeout):
        requests.append(url)
        response = responses[url]
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, int):
            raise HTTPError(url, response, "Test response", {}, None)
        return io.BytesIO(response)

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(published, "urlopen", open_url)
    monkeypatch.setattr(published.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(published.time, "sleep", sleep)
    return responses, requests, now


def serve_release(
    responses, candidates, *, index="testpypi", name="roboz", version="1.2.3"
):
    base, host = published.INDEXES[index]
    records = []
    for path in candidates.iterdir():
        record = {
            "filename": path.name,
            "url": f"https://{host}/{path.name}",
            "digests": {"sha256": published.digest(path)},
            "yanked": False,
        }
        records.append(record)
        responses[record["url"]] = path.read_bytes()
    url = f"{base}/pypi/{name}/{version}/json"
    responses[url] = json.dumps({"urls": records}).encode()
    return url, records


def test_download_checks_both_archives_after_index_delay(
    index_server, candidates, tmp_path
):
    responses, requests, now = index_server
    url, records = serve_release(responses, candidates)
    responses[url] = [404, json.dumps({"urls": records[:1]}).encode(), responses[url]]
    wheel = published.download_verified(
        published.Index("testpypi"), "roboz", "1.2.3", candidates, tmp_path / "download"
    )
    assert wheel.suffix == ".whl"
    assert {p.name: p.read_bytes() for p in wheel.parent.iterdir()} == {
        p.name: p.read_bytes() for p in candidates.iterdir()
    }
    assert now[0] == 20
    assert requests.count(url) == 3


@pytest.mark.parametrize("conflict", ["metadata", "download", "yanked", "wrong-index"])
def test_artifact_failure_is_not_retried(index_server, candidates, tmp_path, conflict):
    responses, _, now = index_server
    url, records = serve_release(responses, candidates)
    if conflict == "metadata":
        records[0]["digests"]["sha256"] = "0" * 64
    elif conflict == "download":
        responses[records[0]["url"]] = b"different bytes"
    elif conflict == "yanked":
        records[0]["yanked"] = True
    else:
        records[0]["url"] = "https://files.pythonhosted.org/wrong-index.whl"
    responses[url] = json.dumps({"urls": records}).encode()
    with pytest.raises(ValueError):
        published.download_verified(
            published.Index("testpypi"),
            "roboz",
            "1.2.3",
            candidates,
            tmp_path / "download",
        )
    assert now[0] == 0


@pytest.mark.parametrize("status", [404, 503])
def test_missing_or_unavailable_index_has_bounded_retries(
    index_server, candidates, tmp_path, status
):
    responses, _, now = index_server
    url, _ = serve_release(responses, candidates)
    responses[url] = status
    with pytest.raises(TimeoutError, match="timed out"):
        published.download_verified(
            published.Index("testpypi", wait_seconds=30),
            "roboz",
            "1.2.3",
            candidates,
            tmp_path / "download",
        )
    assert now[0] == 30


def test_production_version_check_distinguishes_missing_from_server_error(index_server):
    responses, _, now = index_server
    url = "https://pypi.org/pypi/roboz/1.2.3/json"
    responses[url] = [503, 404]
    published.check_unpublished(published.Index("pypi"), "roboz", "1.2.3")
    assert now[0] == 10
    responses[url] = b'{"urls": []}'
    with pytest.raises(ValueError, match="already exists"):
        published.check_unpublished(published.Index("pypi"), "roboz", "1.2.3")
    responses[url] = 403
    with pytest.raises(HTTPError):
        published.check_unpublished(published.Index("pypi"), "roboz", "1.2.3")


@pytest.mark.parametrize("existing_count", [0, 1, 2])
def test_prepare_upload_reuses_only_matching_archives(
    index_server, candidates, tmp_path, existing_count
):
    responses, _, _ = index_server
    url, records = serve_release(responses, candidates)
    responses[url] = (
        json.dumps({"urls": records[:existing_count]}).encode()
        if existing_count
        else 404
    )
    output = tmp_path / "upload"
    needed = published.prepare_upload(
        published.Index("testpypi"), "roboz", "1.2.3", candidates, output
    )
    assert needed == (existing_count < 2)
    assert {p.name for p in output.iterdir()} == {
        r["filename"] for r in records[existing_count:]
    }
    for path in output.iterdir():
        assert path.read_bytes() == (candidates / path.name).read_bytes()


def test_conflicting_partial_release_does_not_stage_upload(
    index_server, candidates, tmp_path
):
    responses, _, _ = index_server
    url, records = serve_release(responses, candidates)
    records[0]["digests"]["sha256"] = hashlib.sha256(b"old release").hexdigest()
    responses[url] = json.dumps({"urls": records[:1]}).encode()
    with pytest.raises(ValueError, match="prepare a new version"):
        published.prepare_upload(
            published.Index("testpypi"),
            "roboz",
            "1.2.3",
            candidates,
            tmp_path / "upload",
        )
    assert not (tmp_path / "upload").exists()


def test_staged_dependencies_use_only_testpypi_and_exclude_extras(
    index_server, tmp_path, monkeypatch
):
    responses, requests, _ = index_server
    projects = {}
    for name, dependencies in {
        "roboz": [],
        "roboz-shed": ["roboz>=1"],
        "roboz-proton-bridge": ["roboz>=1", "roboz-shed>=1", "pydantic>=2"],
    }.items():
        root = tmp_path / name
        root.mkdir()
        (root / "pyproject.toml").write_text(
            f'[project]\nversion = "1.2.3"\ndependencies = {json.dumps(dependencies)}\n'
            '[project.optional-dependencies]\nopenai = ["roboz-openai>=1"]\n'
        )
        projects[name] = root
        dist = root / "dist"
        dist.mkdir()
        (dist / published.filenames(name, "1.2.3")[0]).write_bytes(name.encode())
        serve_release(responses, dist, name=name)
    monkeypatch.setattr(published, "PROJECTS", projects)
    monkeypatch.setattr(release_package, "PROJECTS", projects)
    output = tmp_path / "download"
    output.mkdir()
    paths = published.staged_dependencies(
        published.Index("testpypi"), "roboz-proton-bridge", output
    )
    assert {path.read_bytes() for path in paths} == {b"roboz", b"roboz-shed"}
    assert all(
        "test.pypi.org" in url or "test-files.pythonhosted.org" in url
        for url in requests
    )


def test_installed_contracts_use_isolated_environment_and_propagate_failures(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://unexpected.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "/workspace/source")

    def run(command, *, cwd, env, check):
        assert check and cwd == tmp_path
        assert "PIP_EXTRA_INDEX_URL" not in env and "PYTHONPATH" not in env
        assert env["PIP_CONFIG_FILE"] == published.os.devnull
        calls.append(command)
        if "pytest" in command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(published.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        published.check_install(
            "roboz-openai", "0.1.0a1", tmp_path / "candidate.whl", [], tmp_path
        )
    package_install = next(
        command for command in calls if str(tmp_path / "candidate.whl") in command
    )
    assert "https://pypi.org/simple/" in package_install
    assert "--extra-index-url" not in package_install
    assert not any(
        "candidate-dist" in argument for command in calls for argument in command
    )
    assert any(
        "test_endpoints.py" in argument for command in calls for argument in command
    )
