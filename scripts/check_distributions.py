"""Verify wheel metadata and staged installation outside the source checkout."""

import argparse
import email
import os
import shutil
import tarfile
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboshed": ROOT / "packages/shed",
    "roboz-openai": ROOT / "packages/openai",
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
        wheels[name] = wheel
    return wheels


CORE_SMOKE = """
from importlib.util import find_spec
import roboz as rz
from roboz.llm import MockLLMEndpoint
assert all(find_spec(n) is None for n in ('roboshed', 'roboz_openai', 'roboz_proton_bridge', 'openai', 'pydantic_settings', 'fastapi'))
agent = rz.Agent(name='test', tools=[rz.stop], system_prompt='Stop.', agent_endpoint=MockLLMEndpoint([{'action': 'stop', 'rationale': 'test', 'value': 'ok'}]))
assert agent.invoke()[0].value == 'ok'
"""


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
            "openai": ["roboz", "roboz-openai"],
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
            requirements[0] += "[shed,openai,proton-bridge-beta]"
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
assert all(find_spec(n) is None for n in ('openai', 'pydantic_settings', 'roboz_openai', 'roboz_proton_bridge'))
""")
            workflow = root / "shed_workflows.py"
            shutil.copyfile(ROOT / "tests/e2e/test_shed_workflows.py", workflow)
            run(str(python), "-I", str(workflow))
            # Verify the published console entry point too.
            executable = python.parent / (
                "roboz-demo.exe" if os.name == "nt" else "roboz-demo"
            )
            run(
                str(executable),
                "--mock",
                "--workspace",
                str(root / "workspace"),
                "--data-path",
                str(root / "data"),
            )
            assert list((root / "data").rglob("*.json"))
        if label in {"openai", "extras"}:
            check("""
from roboz_openai import openrouter_endpoint
endpoint = openrouter_endpoint(model='test/model', max_context_tokens=4096)
assert endpoint.dependency_id == 'model:openrouter:test/model'
assert 'materialized' not in endpoint.__dict__
""")
        if label == "openai":
            check(
                "from importlib.util import find_spec; assert find_spec('roboshed') is None"
            )
        if label == "proton":
            check("""
from importlib.util import find_spec
from roboz_proton_bridge import ProtonBridgeEmailService
assert ProtonBridgeEmailService().dependency_id
assert all(find_spec(n) is None for n in ('openai', 'roboz_openai', 'firecrawl', 'pymupdf4llm', 'groq', 'cerebras'))
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
