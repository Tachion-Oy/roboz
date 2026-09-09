"""Verify wheel metadata and staged installation outside the source checkout."""

import argparse
import email
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboshed": ROOT / "packages/shed",
    "roboz-endpoints": ROOT / "packages/endpoints",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}


def wheels_for(dist: Path, *, core_only: bool = False) -> dict[str, Path]:
    wheels = {}
    for name, root in PROJECTS.items():
        if core_only and name != "roboz":
            continue
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        namespace = name.replace("-", "_")
        wheel = dist / f"{namespace}-{project['version']}-py3-none-any.whl"
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata = email.message_from_bytes(
                archive.read(next(n for n in names if n.endswith("/METADATA")))
            )
            assert metadata["Name"] == name
            assert metadata["Version"] == project["version"]
            assert metadata["Requires-Python"] == project["requires-python"]
            assert metadata["Import-Name"] == namespace
            assert metadata["License-Expression"] == "Apache-2.0"
            assert f"{namespace}/py.typed" in names
            assert any(n.endswith(".dist-info/licenses/LICENSE") for n in names)
            assert all(
                n.startswith(
                    (f"{namespace}/", f"{namespace}-{project['version']}.dist-info/")
                )
                for n in names
            )
            requirements = metadata.get_all("Requires-Dist", [])
            assert all(
                " @ " not in requirement and "file:" not in requirement
                for requirement in requirements
            )
            if name == "roboz-endpoints":
                assert metadata.get_all("Provides-Extra") == ["openai"]
                assert {
                    "roboz_endpoints/catalog.py",
                    "roboz_endpoints/catalog.pyi",
                    "roboz_endpoints/inventory.pyi",
                    "roboz_endpoints/__init__.pyi",
                    "roboz_endpoints/specs.py",
                    "roboz_endpoints/inventory.py",
                    "roboz_endpoints/adapters/openai_compatible.py",
                    "roboz_endpoints/cli.py",
                    "roboz_endpoints/__main__.py",
                    "roboz_endpoints/_inventory_codec.py",
                    "roboz_endpoints/_inventory_codegen.py",
                } <= set(names)
                entry_points = archive.read(next(n for n in names if n.endswith("/entry_points.txt"))).decode()
                assert "roboz-endpoints = roboz_endpoints.cli:main" in entry_points
        wheels[name] = wheel
    return wheels


CORE_SMOKE = """
from importlib.util import find_spec
import roboz as rz
from roboz.dependencies import DependencyRegistration, DependencyRoute, ExecutableDependency, ExternalDependencyKind, LazyExternalDependency, bind_dependencies
from roboz.llm import LLMEndpoint, ModelSelector
assert find_spec("roboz.tooling.dependencies") is None
resource = ExecutableDependency("python")
route = DependencyRoute(lambda: resource)
assert route.materialize() is resource
assert route.external_dependencies() == (resource,)
registration = DependencyRegistration(resource.dependency_id, resource.kind, lambda dependency: True)
bound, = bind_dependencies([resource], [registration])
assert bound.dependency is resource and bound.check is registration.check
def unexpected_resolution():
    raise AssertionError("selection must not construct clients")
model = LazyExternalDependency[LLMEndpoint]("model:test:one", ExternalDependencyKind.MODEL_ENDPOINT, {}, unexpected_resolution)
selector = ModelSelector({"One": model}, default=model)
assert selector.selected_endpoint is model
assert all(find_spec(n) is None for n in ("roboz.agents", "roboz.workspace", "roboz.tools.snapshot_conversations", "roboz.tools.compactification"))
from roboz.llm import MockLLMEndpoint
assert all(find_spec(n) is None for n in ('roboshed', 'roboz_endpoints', 'roboz_proton_bridge', 'openai', 'pydantic_settings', 'fastapi'))
assert find_spec('roboz_openai') is None
agent = rz.Agent(name='test', tools=[rz.stop], system_prompt='Stop.', agent_endpoint=MockLLMEndpoint([{'action': 'stop', 'rationale': 'test', 'value': 'ok'}]))
assert agent.invoke()[0].value == 'ok'
"""


ENDPOINTS_SMOKE = """
import importlib, pkgutil, sys
from importlib.util import find_spec
import roboz_endpoints
from roboz_endpoints import openrouter, cerebras, groq
from roboz import LazyExternalDependency
for module in pkgutil.walk_packages(roboz_endpoints.__path__, roboz_endpoints.__name__ + '.'):
    importlib.import_module(module.name)
assert find_spec('roboz_openai') is None
assert 'openai' not in sys.modules
endpoints = (
    openrouter.z_ai__glm_5_3, openrouter.z_ai__glm_5_3_flash,
    cerebras.gpt_oss_120b, groq.whisper_large_v3_turbo,
)
assert sum(len(provider.models) for provider in (openrouter, cerebras, groq)) == 4
for endpoint in endpoints:
    assert isinstance(endpoint, LazyExternalDependency)
    assert 'materialized' not in endpoint.__dict__
    assert endpoint.external_dependencies() == (endpoint,)
    metadata = endpoint.redacted_metadata()
    assert endpoint.dependency_id == f"model:{metadata['api_name']}:{metadata['model_name']}"
"""

ENDPOINTS_BASE_SMOKE = (
    ENDPOINTS_SMOKE
    + """
assert all(find_spec(n) is None for n in ('openai', 'groq', 'cerebras'))
for endpoint in endpoints:
    try:
        endpoint.materialize()
    except ModuleNotFoundError as error:
        assert error.name == 'openai'
        assert "pip install 'roboz-endpoints[openai]'" in str(error)
    else:
        raise AssertionError('SDK-free materialization should explain the missing extra')
"""
)


def check_endpoint_types(python: Path, root: Path, env: dict[str, str]) -> None:
    """Check consumer typing against installed packages outside the checkout."""
    root.mkdir()
    config = root / "pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "standard", "pythonVersion": "3.13"})
    )
    cases = ROOT / "tests/type_tests/cases"
    valid = root / "valid.py"
    shutil.copyfile(cases / "valid/test_endpoint_catalog.py", valid)
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(config),
        "--pythonpath",
        str(python),
    ]
    subprocess.run([*command, str(valid)], cwd=root, env=env, check=True)
    for name, rule in (
        ("test_unknown_catalogue_model.py", "reportAttributeAccessIssue"),
        ("test_transcription_as_chat.py", "reportArgumentType"),
    ):
        invalid = root / name
        shutil.copyfile(cases / "expected_failures" / name, invalid)
        result = subprocess.run(
            [*command, "--outputjson", str(invalid)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        errors = [
            diagnostic
            for diagnostic in json.loads(result.stdout)["generalDiagnostics"]
            if diagnostic["severity"] == "error"
        ]
        assert len(errors) == 1 and errors[0].get("rule") == rule, errors
    check_inventory_workflow(python, root, env, command)


def check_inventory_workflow(python: Path, root: Path, env: dict[str, str], type_command: list[str]) -> None:
    """Exercise installed inventory commands, generated typing, and confirmed reset."""
    project = root / "inventory-project"
    project.mkdir()
    executable = python.with_name("roboz-endpoints.exe" if os.name == "nt" else "roboz-endpoints")

    def command(*args: str, answer: str = "", success: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(executable), "inventory", *args], input=answer,
            cwd=project, env=env, capture_output=True, text=True,
        )
        assert result.returncode == success, result.stdout + result.stderr
        return result

    command("export")
    editable = project / "models.json"
    bundled = editable.read_bytes()
    data = json.loads(bundled)
    fixture = ROOT / "tests/type_tests/fixtures/models.json"
    data["providers"].update(json.loads(fixture.read_text())["providers"])
    editable.write_text(json.dumps(data))
    command("import")
    module = project / "project_models.py"
    command("export", "--from-module", str(module), "--path", "roundtrip.json")
    exported = json.loads((project / "roundtrip.json").read_text())
    assert exported["providers"]["custom"]["timeout_s"] == 12
    assert exported["providers"]["groq"]["models"] == data["providers"]["groq"]["models"]
    assert list(exported["providers"]) == list(data["providers"])

    def inspect(code: str) -> None:
        subprocess.run(
            [str(python), "-I", "-c", f"import sys; sys.path.insert(0, {str(project)!r})\n" + code],
            cwd=root, env=env, check=True,
        )

    inspect("""
import project_models as models
assert models.custom.chat.dependency_id == 'model:custom:custom/chat'
assert models.custom.audio.redacted_metadata()['endpoint_type'] == 'transcription'
assert models.custom.chat is models.custom.chat
assert 'openai' not in sys.modules
assert 'materialized' not in models.custom.chat.__dict__
""")
    cases = ROOT / "tests/type_tests/cases"
    for case, expected, rule in (
        ("valid/test_user_inventory.py", 0, None),
        ("expected_failures/test_unknown_user_inventory_model.py", 1, "reportAttributeAccessIssue"),
        ("expected_failures/test_user_inventory_transcription_as_chat.py", 1, "reportArgumentType"),
    ):
        target = project / Path(case).name
        target.write_text((cases / case).read_text().replace("tests.type_tests.fixtures.inventory_models", "project_models"))
        checked = subprocess.run([*type_command, "--outputjson", str(target)], cwd=project, env=env, capture_output=True, text=True)
        assert checked.returncode == expected, checked.stdout + checked.stderr
        if rule:
            errors = [d for d in json.loads(checked.stdout)["generalDiagnostics"] if d["severity"] == "error"]
            assert len(errors) == 1 and errors[0].get("rule") == rule, errors
    before = (editable.read_bytes(), module.read_bytes())
    command("reset", answer="no\n", success=1)
    assert (editable.read_bytes(), module.read_bytes()) == before
    command("reset", answer="yes\n")
    assert editable.read_bytes() == bundled
    command("export", "--from-module", str(module), "--path", "reset.json")
    assert (project / "reset.json").read_bytes() == bundled
    inspect("""
import project_models as models
assert not hasattr(models, 'custom')
assert not hasattr(models.groq, 'new_chat')
assert models.groq.whisper_large_v3_turbo.dependency_id
assert 'openai' not in sys.modules
""")


def check_installs(dist: Path, root: Path, *, core_only: bool = False) -> None:
    wheels = wheels_for(dist, core_only=core_only)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    # Each companion must work with only its own declared dependency closure.
    cases = (
        {"core": ["roboz"]}
        if core_only
        else {
            "core": ["roboz"],
            "shed": ["roboz", "roboshed"],
            "endpoints": ["roboz", "roboz-endpoints"],
            "endpoints-openai": ["roboz", "roboz-endpoints"],
            "proton": ["roboz", "roboshed", "roboz-proton-bridge"],
            "extras": list(wheels),
        }
    )
    for label, packages in cases.items():
        venv = root / label
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(*command: str) -> None:
            subprocess.run(command, cwd=root, env=env, check=True)

        def check(code: str) -> None:
            run(str(python), "-I", "-c", code)

        run("uv", "venv", "--seed", "--python", sys.executable, str(venv))
        requirements = [str(wheels[name]) for name in packages]
        if label == "extras":
            requirements[0] += "[shed,proton-bridge-beta]"
        if label in {"endpoints-openai", "extras"}:
            requirements[packages.index("roboz-endpoints")] += "[openai]"
        run(str(python), "-I", "-m", "pip", "install", *requirements)
        run(str(python), "-I", "-m", "pip", "check")
        check(f"""
import importlib, importlib.metadata as metadata, json, sys
from pathlib import Path
for name in {packages!r}:
    module = importlib.import_module(name.replace('-', '_'))
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__file__
    source = json.loads(metadata.distribution(name).read_text('direct_url.json'))
    assert 'dir_info' not in source and source['url'].endswith('.whl')
""")
        if label == "core":
            check(CORE_SMOKE)
            workflow = root / "core_workflows.py"
            shutil.copyfile(ROOT / "tests/e2e/test_core_workflows.py", workflow)
            run(str(python), "-I", str(workflow))
        if label == "shed":
            check("""
from importlib.util import find_spec
import importlib, pkgutil, roboshed
for module in pkgutil.walk_packages(roboshed.__path__, roboshed.__name__ + '.'):
    importlib.import_module(module.name)
assert all(find_spec(n) is None for n in ('openai', 'pydantic_settings', 'roboz_endpoints', 'roboz_proton_bridge', 'fastapi', 'robosprawl'))
""")
            workflow = root / "shed_workflows.py"
            shutil.copyfile(ROOT / "tests/e2e/test_shed_workflows.py", workflow)
            run(str(python), "-I", str(workflow))
        if label == "endpoints":
            check(ENDPOINTS_BASE_SMOKE)
        if label in {"endpoints-openai", "extras"}:
            check(ENDPOINTS_SMOKE)
            check(
                "from importlib.util import find_spec; assert find_spec('openai') is not None"
            )
        if label in {"endpoints", "endpoints-openai"}:
            check(
                "from importlib.util import find_spec; assert find_spec('roboshed') is None"
            )
            check_endpoint_types(python, root / f"{label}-typing", env)
        if label == "endpoints-openai":
            # Run the same standalone SDK contracts used by indexed-release checks.
            contract = root / "test_endpoints.py"
            shutil.copyfile(
                ROOT / "packages/endpoints/tests/test_endpoints.py", contract
            )
            config = root / "pytest.ini"
            config.write_text("[pytest]\n")
            run(str(python), "-I", "-m", "pip", "install", "pytest>=7")
            run(str(python), "-I", "-m", "pytest", "-c", str(config), str(contract))
        if label == "proton":
            check("""
from importlib.util import find_spec
from roboz_proton_bridge import ProtonBridgeEmailService
assert ProtonBridgeEmailService().dependency_id
assert all(find_spec(n) is None for n in ('openai', 'roboz_endpoints', 'firecrawl', 'pymupdf4llm', 'groq', 'cerebras'))
""")
        print(f"PASS {dist.name}: independent {label} pip installation", flush=True)


def rebuild_sdists(dist: Path, root: Path) -> Path:
    rebuilt = root / "rebuilt"
    for name, project_root in PROJECTS.items():
        project = tomllib.loads((project_root / "pyproject.toml").read_text())[
            "project"
        ]
        stem = f"{name.replace('-', '_')}-{project['version']}"
        with tarfile.open(dist / f"{stem}.tar.gz") as archive:
            archive.extractall(root / "sources", filter="data")
        subprocess.run(
            [
                "uv",
                "build",
                "--no-sources",
                "--wheel",
                "--out-dir",
                str(rebuilt),
                str(root / "sources" / stem),
            ],
            cwd=root,
            check=True,
        )
    wheels_for(rebuilt)
    return rebuilt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--core-only", action="store_true", help="Portable core wheel contract"
    )
    args = parser.parse_args()
    dist = args.dist.resolve()
    with tempfile.TemporaryDirectory(prefix="roboz-wheel-check-") as directory:
        root = Path(directory)
        original = root / "original"
        original.mkdir()
        check_installs(dist, original, core_only=args.core_only)
        if not args.core_only:
            rebuilt = rebuild_sdists(dist, root)
            installs = root / "sdist-installs"
            installs.mkdir()
            check_installs(rebuilt, installs)


if __name__ == "__main__":
    main()
