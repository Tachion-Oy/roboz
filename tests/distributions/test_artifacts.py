import email
from pathlib import Path
import tarfile
import zipfile

from scripts.release_package import artifacts, project_metadata


def test_package_contents_and_metadata(pytestconfig, package):
    wheel, sdist = artifacts(Path(pytestconfig.getoption("--dist")), package)
    project = project_metadata(package)
    namespace = package.replace("-", "_")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = email.message_from_bytes(
            archive.read(next(name for name in names if name.endswith("/METADATA")))
        )
        for field, expected in {
            "Name": package,
            "Version": project["version"],
            "Requires-Python": project["requires-python"],
            "Import-Name": namespace,
            "License-Expression": "Apache-2.0",
        }.items():
            assert metadata[field] == expected
        assert f"{namespace}/py.typed" in names
        if package == "roboz":
            assert f"{namespace}/__init__.pyi" in names
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert all(
            name.startswith(
                (namespace + "/", f"{namespace}-{project['version']}.dist-info/")
            )
            for name in names
        )
        assert all(
            " @ " not in requirement and "file:" not in requirement
            for requirement in metadata.get_all("Requires-Dist", [])
        )
        if package == "roboz-endpoints":
            assert metadata.get_all("Provides-Extra") == ["openai"]
            assert {
                f"{namespace}/{file}"
                for file in (
                    "__init__.pyi",
                    "__main__.py",
                    "_inventory_codec.py",
                    "_inventory_codegen.py",
                    "adapters/openai_compatible.py",
                    "catalog.py",
                    "catalog.pyi",
                    "cli.py",
                    "inventory.py",
                    "inventory.pyi",
                    "specs.py",
                )
            } <= names
            entry_points = archive.read(
                next(name for name in names if name.endswith("/entry_points.txt"))
            ).decode()
            assert "roboz-endpoints = roboz_endpoints.cli:main" in entry_points
    with tarfile.open(sdist) as archive:
        paths = [Path(name).parts[1:] for name in archive.getnames()]
        assert all(not path or path[0] not in {"scripts", ".github"} for path in paths)
        assert ("src", namespace, "py.typed") in paths
        if package == "roboz":
            assert ("src", namespace, "__init__.pyi") in paths
