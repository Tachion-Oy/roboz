"""Verify wheel metadata and staged installation outside the source checkout."""

import argparse
import email
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboz-shed": ROOT / "packages/shed",
    "roboz-openai": ROOT / "packages/openai",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}


def wheels_for(dist: Path) -> dict[str, Path]:
    wheels = {}
    for name, root in PROJECTS.items():
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    wheels = wheels_for(args.dist.resolve())
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "VIRTUAL_ENV"}
    }

    with tempfile.TemporaryDirectory(prefix="roboz-wheel-check-") as directory:
        root = Path(directory)
        python = (
            root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )

        def run(*command: str) -> None:
            subprocess.run(command, cwd=root, env=env, check=True)

        def check(code: str) -> None:
            run(str(python), "-I", "-c", code)

        def install(*requirements: str) -> None:
            run("uv", "pip", "install", "--python", str(python), *requirements)

        run("uv", "venv", "--python", sys.executable, str(root / "venv"))
        install(str(wheels["roboz"]))
        check("""
from importlib.util import find_spec
import roboz as rz
from roboz.llm import MockLLMEndpoint
assert all(find_spec(n) is None for n in ('roboz_shed', 'roboz_openai', 'roboz_proton_bridge', 'openai', 'pydantic_settings', 'fastapi'))
agent = rz.Agent(name='test', tools=[rz.stop], system_prompt='Stop.', agent_endpoint=MockLLMEndpoint([{'action': 'stop', 'rationale': 'test', 'value': 'ok'}]))
assert agent.invoke()[0].value == 'ok'
""")
        print("PASS core-only wheel installation", flush=True)

        install(str(wheels["roboz-shed"]))
        check("""
from importlib.util import find_spec
import importlib, pkgutil, roboz_shed
for module in pkgutil.walk_packages(roboz_shed.__path__, roboz_shed.__name__ + '.'):
    importlib.import_module(module.name)
assert all(find_spec(n) is None for n in ('openai', 'pydantic_settings', 'roboz_openai', 'roboz_proton_bridge'))
""")
        run(
            str(python),
            "-I",
            "-m",
            "roboz_shed.demo",
            "--mock",
            "--workspace",
            str(root / "workspace"),
            "--data-path",
            str(root / "data"),
        )
        assert list((root / "workspace").glob("roboz-demo-*.txt"))
        assert list((root / "data").rglob("*.json"))
        print("PASS Shed-only installed demo", flush=True)

        install(str(wheels["roboz-proton-bridge"]))
        check("""
from importlib.util import find_spec
from roboz_proton_bridge import ProtonBridgeEmailService
assert ProtonBridgeEmailService().dependency_id
assert all(find_spec(n) is None for n in ('openai', 'firecrawl', 'pymupdf4llm', 'groq', 'cerebras'))
""")
        print("PASS Proton without unrelated integrations", flush=True)

        install(str(wheels["roboz-openai"]))
        check("""
from roboz_openai import openrouter_endpoint
endpoint = openrouter_endpoint(model='test/model', max_context_tokens=4096)
assert endpoint.dependency_id == 'model:openrouter:test/model'
assert 'materialized' not in endpoint.__dict__
""")
        run("uv", "pip", "check", "--python", str(python))
        check("""
import importlib.metadata as metadata, json
for name in ('roboz', 'roboz-shed', 'roboz-openai', 'roboz-proton-bridge'):
    source = json.loads(metadata.distribution(name).read_text('direct_url.json'))
    assert 'dir_info' not in source
    assert source['url'].endswith('.whl')
""")
        # Re-resolve extras using exact local wheel references for all workspace
        # projects; never accidentally substitute a same-version PyPI package.
        install(
            str(wheels["roboz"]) + "[shed,openai,proton-bridge-beta]",
            *(str(wheel) for name, wheel in wheels.items() if name != "roboz"),
        )
        print("PASS adapter, extras, metadata, and dependency consistency", flush=True)


if __name__ == "__main__":
    main()
