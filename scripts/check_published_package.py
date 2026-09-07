"""Verify one indexed release and its installation without workspace dependencies.

Run as ``python -m scripts.check_published_package`` from the repository root.
All index reads are anonymous. Uploads belong to the isolated publishing jobs.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from scripts.check_distributions import CORE_SMOKE
from scripts.release_package import PROJECTS, ROOT, project_version

INDEXES = {
    "pypi": ("https://pypi.org", "files.pythonhosted.org"),
    "testpypi": ("https://test.pypi.org", "test-files.pythonhosted.org"),
}


class Unavailable(RuntimeError):
    """An index or newly uploaded file is temporarily unavailable."""


class Index:
    def __init__(self, name: str, *, wait_seconds: float = 180):
        self.base, self.file_host = INDEXES[name]
        self.deadline = time.monotonic() + wait_seconds

    def retry(self, operation):
        while True:
            try:
                return operation()
            except Unavailable as error:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Index availability check timed out: {error}"
                    ) from error
                print(f"Waiting for index availability: {error}", flush=True)
                time.sleep(min(10, remaining))

    def read(self, url: str) -> bytes | None:
        try:
            with urlopen(
                url, timeout=max(0.1, min(30, self.deadline - time.monotonic()))
            ) as response:
                return response.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in {408, 429} or error.code >= 500:
                raise Unavailable(f"HTTP {error.code} from {url}") from error
            raise
        except (URLError, TimeoutError, ConnectionError) as error:
            raise Unavailable(f"Cannot read {url}: {error}") from error

    def release(self, name: str, version: str) -> dict | None:
        raw = self.read(
            f"{self.base}/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
        )
        if raw is None:
            return None
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("urls"), list):
            raise ValueError(f"Malformed release metadata for {name} {version}")
        return result

    def download(self, record: dict, output: Path) -> Path:
        parsed = urlparse(record["url"])
        if parsed.scheme != "https" or parsed.netloc != self.file_host:
            raise ValueError(f"Unexpected artifact source: {record['url']}")
        if record.get("yanked"):
            raise ValueError(f"Refusing yanked artifact: {output.name}")
        data = self.read(record["url"])
        if data is None:
            raise Unavailable(f"Artifact is not available yet: {output.name}")
        if hashlib.sha256(data).hexdigest() != record["digests"]["sha256"]:
            raise ValueError(f"Downloaded artifact hash mismatch: {output.name}")
        output.write_bytes(data)
        return output


def filenames(name: str, version: str) -> tuple[str, str]:
    stem = f"{name.replace('-', '_')}-{version}"
    return f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_unpublished(index: Index, name: str, version: str) -> None:
    if index.retry(lambda: index.release(name, version)) is not None:
        raise ValueError(
            f"{name} {version} already exists on PyPI; prepare a new version"
        )


def prepare_upload(
    index: Index, name: str, version: str, candidates: Path, output: Path
) -> bool:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Upload directory must be empty: {output}")
    expected = filenames(name, version)
    hashes = {filename: digest(candidates / filename) for filename in expected}
    release = index.retry(lambda: index.release(name, version))
    existing = (
        {entry["filename"]: entry for entry in release["urls"]} if release else {}
    )
    # Validate all existing files before staging any upload, including partial releases.
    for filename in expected:
        if filename in existing:
            if (
                existing[filename].get("yanked")
                or existing[filename]["digests"]["sha256"] != hashes[filename]
            ):
                raise ValueError(
                    f"TestPyPI contains different or yanked bytes for {filename}; prepare a new version"
                )
    output.mkdir(parents=True, exist_ok=True)
    missing = [filename for filename in expected if filename not in existing]
    for filename in missing:
        shutil.copyfile(candidates / filename, output / filename)
    return bool(missing)


def download_verified(
    index: Index, name: str, version: str, candidates: Path, output: Path
) -> Path:
    expected = filenames(name, version)
    hashes = {filename: digest(candidates / filename) for filename in expected}
    output.mkdir(parents=True, exist_ok=True)

    def download():
        release = index.release(name, version)
        records = (
            {entry["filename"]: entry for entry in release["urls"]} if release else {}
        )
        for filename in expected:
            if (
                filename in records
                and records[filename]["digests"]["sha256"] != hashes[filename]
            ):
                raise ValueError(
                    f"Published artifact differs from verified candidate: {filename}"
                )
        if not all(filename in records for filename in expected):
            raise Unavailable(f"Waiting for both archives of {name} {version}")
        for filename in expected:
            index.download(records[filename], output / filename)
        return output / expected[0]

    return index.retry(download)


def workspace_dependencies(name: str) -> list[str]:
    """Return the required Roboz dependency closure, excluding optional extras."""
    found: list[str] = []

    def visit(package: str):
        project = tomllib.loads((PROJECTS[package] / "pyproject.toml").read_text())[
            "project"
        ]
        for requirement in project.get("dependencies", []):
            match = re.match(r"[A-Za-z0-9_.-]+", requirement)
            dependency = re.sub(r"[-_.]+", "-", match[0]).lower() if match else ""
            if (
                dependency in PROJECTS
                and dependency not in found
                and dependency != name
            ):
                found.append(dependency)
                visit(dependency)

    visit(name)
    return found


def staged_dependencies(index: Index, name: str, output: Path) -> list[Path]:
    paths = []
    for dependency in workspace_dependencies(name):
        version = project_version(dependency)
        filename = filenames(dependency, version)[0]

        def download():
            release = index.release(dependency, version)
            records = (
                {entry["filename"]: entry for entry in release["urls"]}
                if release
                else {}
            )
            if filename not in records:
                raise Unavailable(
                    f"Stage {dependency} {version} on TestPyPI before rehearsing {name}"
                )
            return index.download(records[filename], output / filename)

        paths.append(index.retry(download))
    return paths


def consumer_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST_", "PIP_", "UV_"))
        and key != "VIRTUAL_ENV"
    }
    env.update(PIP_CONFIG_FILE=os.devnull, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return env


def check_install(
    name: str, version: str, wheel: Path, dependencies: list[Path], root: Path
) -> None:
    env = consumer_environment()
    venv = root / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def run(*command: str):
        subprocess.run(command, cwd=root, env=env, check=True)

    def check(code: str):
        run(str(python), "-I", "-c", code)

    def install(*requirements: str):
        run(
            str(python),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "install",
            "--index-url",
            "https://pypi.org/simple/",
            *requirements,
        )

    run(sys.executable, "-I", "-m", "venv", str(venv))
    install(str(wheel), *(str(path) for path in dependencies))
    run(str(python), "-I", "-m", "pip", "--isolated", "check")
    check(f"""
import importlib, importlib.metadata as metadata, sys
from pathlib import Path
assert metadata.version({name!r}) == {version!r}
for name in {[name, *workspace_dependencies(name)]!r}:
    module = importlib.import_module(name.replace('-', '_'))
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__file__
""")
    if name == "roboz":
        check(CORE_SMOKE)
    if name in {"roboz", "roboshed"}:
        source = (
            "test_core_workflows.py" if name == "roboz" else "test_shed_workflows.py"
        )
        workflow = root / source
        shutil.copyfile(ROOT / "tests/e2e" / source, workflow)
        run(str(python), "-I", str(workflow))
    else:
        # Exercise real adapters with scripted HTTP/IMAP, without provider credentials.
        source = (
            "test_endpoints.py"
            if name == "roboz-openai"
            else "test_proton_bridge_email.py"
        )
        contract = root / source
        shutil.copyfile(PROJECTS[name] / "tests" / source, contract)
        config = root / "pytest.ini"
        config.write_text("[pytest]\n")
        install("pytest>=7")
        run(str(python), "-I", "-m", "pytest", "-c", str(config), str(contract))
    print(f"PASS: installed {name} {version} with published dependencies", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, choices=PROJECTS)
    parser.add_argument("--index", choices=INDEXES, default="testpypi")
    parser.add_argument("--dependency-index", choices=INDEXES, default="pypi")
    parser.add_argument("--candidates", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-unpublished", action="store_true")
    modes.add_argument("--prepare-upload", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    name, version = args.package, project_version(args.package)
    if args.check_unpublished:
        check_unpublished(Index("pypi"), name, version)
        return
    if not args.candidates:
        parser.error("--candidates is required for artifact checks")
    if args.prepare_upload:
        needed = prepare_upload(
            Index("testpypi"), name, version, args.candidates, args.prepare_upload
        )
        if args.github_output:
            with args.github_output.open("a") as output:
                output.write(f"upload_needed={str(needed).lower()}\n")
        print(
            "TestPyPI upload prepared"
            if needed
            else "Matching TestPyPI archives already exist"
        )
        return
    with tempfile.TemporaryDirectory(prefix="roboz-index-check-") as directory:
        root = Path(directory)
        index = Index(args.index)
        wheel = download_verified(index, name, version, args.candidates, root)
        dependencies = (
            staged_dependencies(
                index if args.index == "testpypi" else Index("testpypi"), name, root
            )
            if args.dependency_index == "testpypi"
            else []
        )
        check_install(name, version, wheel, dependencies, root)


if __name__ == "__main__":
    main()
