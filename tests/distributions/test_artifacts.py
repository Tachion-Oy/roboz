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
        assert f"{namespace}/__init__.pyi" in names
        assert {
            "roboz/examples/__init__.py",
            "roboz/examples/complex.py",
            "roboz/examples/simple.py",
            "roboz/examples/simpsons_quotes.py",
            "roboz/shed/__init__.py",
            "roboz/shed/capabilities.py",
            "roboz/shed/tools/email/proton_bridge/service.py",
            "roboz/shed/tools/email/proton_bridge/models.py",
            "roboz/endpoints/__main__.py",
            "roboz/endpoints/README.md",
            "roboz/endpoints/adapters/openai_compatible.py",
            "roboz/endpoints/catalog.py",
            "roboz/endpoints/catalog.pyi",
            "roboz/endpoints/inventory.py",
            "roboz/endpoints/inventory.pyi",
        } <= names
        assert "roboz/shed/py.typed" not in names
        assert "roboz/endpoints/py.typed" not in names
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
        assert metadata.get_all("Provides-Extra") is None
        requirements = metadata.get_all("Requires-Dist", [])
        assert any(requirement.startswith("openai<3,>=2.8.1") for requirement in requirements)
        assert any(requirement.startswith("imapclient<5,>=4.1") for requirement in requirements)
        assert not any(name.endswith("/entry_points.txt") for name in names)
    with tarfile.open(sdist) as archive:
        paths = [Path(name).parts[1:] for name in archive.getnames()]
        assert all(not path or path[0] not in {"scripts", ".github"} for path in paths)
        assert ("src", namespace, "py.typed") in paths
        assert ("src", namespace, "__init__.pyi") in paths
        assert ("src", namespace, "examples", "simple.py") in paths
        assert ("src", namespace, "shed", "capabilities.py") in paths
        assert ("src", namespace, "shed", "tools", "email", "proton_bridge", "service.py") in paths
        assert ("src", namespace, "endpoints", "inventory.pyi") in paths
        assert ("src", namespace, "endpoints", "README.md") in paths
        assert all(not path or path[0] != "packages" for path in paths)
