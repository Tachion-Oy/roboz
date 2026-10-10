from configparser import ConfigParser
import email
from pathlib import Path
import tarfile
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

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
            "roboz/shed/tools/cli_commands/contracts.py",
            "roboz/shed/tools/cli_commands/command.py",
            "roboz/shed/skills/filesystem/prompts.py",
            "roboz/shed/skills/filesystem/cli.py",
            "roboz/shed/skills/filesystem/patch.py",
            "roboz/cli.py",
            "roboz/endpoints/adapters/openai_compatible.py",
            "roboz/endpoints/catalog.py",
            "roboz/endpoints/catalog.pyi",
            "roboz/endpoints/inventory.py",
            "roboz/endpoints/inventory.pyi",
        } <= names
        assert not any(name.endswith("/README.md") for name in names)
        for module in (
            "__init__",
            "protocol",
            "client",
            "server",
        ):
            assert f"roboz/shed/tools/safe_scripts/{module}.py" in names
        for package_path in ("roboz/endpoints", "roboz/shed/tools/safe_scripts"):
            assert f"{package_path}/cli.py" not in names
            assert f"{package_path}/__main__.py" not in names
        for template in ("tool.py", "tool_init.py", "skill_init.py", "requirements.txt"):
            assert f"roboz/cli_templates/{template}.tmpl" in names
        assert "roboz/shed/tools/safe_scripts.py" not in names
        assert "roboz/shed/tools/safe_scripts/execution.py" not in names
        assert "roboz/shed/tools/safe_scripts/contracts.py" not in names
        assert not any(
            name.startswith((
                "roboz/shed/tools/cli_commands_v2/",
                "roboz/shed/tools/cli_commands/tagged_transfer/",
                "roboz/shed/tools/cli_commands/run_file_command/",
                "roboz/shed/tools/cli_commands/utilities/",
            ))
            for name in names
        )
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
        assert any(
            requirement.startswith("openai<4,>=2.8.1") for requirement in requirements
        )
        assert not any(
            requirement.startswith("imapclient") for requirement in requirements
        )
        entry_points = ConfigParser()
        entry_points.read_string(archive.read(next(
            name for name in names if name.endswith("/entry_points.txt")
        )).decode())
        assert dict(entry_points["console_scripts"]) == {"roboz": "roboz.cli:main"}
    with tarfile.open(sdist) as archive:
        paths = [Path(name).parts[1:] for name in archive.getnames()]
        assert [path for path in paths if path and path[-1] == "README.md"] == [
            ("README.md",)
        ]
        assert all(not path or path[0] not in {"scripts", ".github"} for path in paths)
        assert ("src", namespace, "py.typed") in paths
        assert ("src", namespace, "__init__.pyi") in paths
        assert ("src", namespace, "examples", "simple.py") in paths
        assert ("src", namespace, "shed", "capabilities.py") in paths
        for module in (
            "__init__",
            "protocol",
            "client",
            "server",
        ):
            assert (
                "src",
                namespace,
                "shed",
                "tools",
                "safe_scripts",
                f"{module}.py",
            ) in paths
        assert ("src", namespace, "endpoints", "inventory.pyi") in paths
        assert all(not path or path[0] != "packages" for path in paths)


@pytest.mark.parametrize(
    ("dependency", "vulnerable", "patched"),
    [
        ("python-dotenv", "1.2.1", "1.2.2"),
        ("pygments", "2.19.2", "2.20.0"),
    ],
)
def test_runtime_security_requirements_exclude_vulnerable_versions(
    pytestconfig, package, dependency, vulnerable, patched
):
    wheel, _ = artifacts(Path(pytestconfig.getoption("--dist")), package)
    with zipfile.ZipFile(wheel) as archive:
        metadata = email.message_from_bytes(
            archive.read(
                next(name for name in archive.namelist() if name.endswith("/METADATA"))
            )
        )
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, metadata.get_all("Requires-Dist", []))
    }
    specifier = requirements[dependency].specifier
    assert vulnerable not in specifier
    assert patched in specifier
