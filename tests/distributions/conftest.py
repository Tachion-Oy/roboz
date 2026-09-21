import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.release_package import PROJECTS, ROOT, artifacts

CASES = {
    "roboz": ("roboz",),
}


def pytest_generate_tests(metafunc):
    package = metafunc.config.getoption("--package")
    if package and package not in PROJECTS:
        raise pytest.UsageError(f"Unknown distribution: {package}")
    if metafunc.config.getoption("--published-dependencies") and not package:
        raise pytest.UsageError("--published-dependencies requires --package")
    if "consumer" in metafunc.fixturenames:
        cases = [
            case
            for case, names in CASES.items()
            if not package or names[-1] == package
        ]
        metafunc.parametrize("consumer", cases, indirect=True, scope="module")
    if "package" in metafunc.fixturenames:
        metafunc.parametrize("package", [package] if package else list(PROJECTS))


@pytest.fixture(scope="module")
def consumer(request, tmp_path_factory):
    case = request.param
    root = tmp_path_factory.mktemp(case)
    venv = root / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST_", "PIP_", "UV_"))
        and key != "VIRTUAL_ENV"
    }
    env.update(
        PIP_CONFIG_FILE=os.devnull,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ROBOZ_INSTALL_CASE=case,
    )
    subprocess.run(
        ["uv", "venv", "--seed", "--python", sys.executable, str(venv)],
        cwd=root,
        env=env,
        check=True,
    )
    names = CASES[case]
    local = (
        names[-1:] if request.config.getoption("--published-dependencies") else names
    )
    dist = Path(request.config.getoption("--dist")).resolve()
    requirements = []
    for name in local:
        wheel, _ = artifacts(dist, name)
        requirements.append(str(wheel))
    subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--index-url",
            "https://pypi.org/simple/",
            *requirements,
            "pytest>=7",
        ],
        cwd=root,
        env=env,
        check=True,
    )
    subprocess.run(
        [str(python), "-I", "-m", "pip", "--isolated", "check"],
        cwd=root,
        env=env,
        check=True,
    )
    (root / "pytest.ini").write_text("[pytest]\n")
    shutil.copyfile(
        ROOT / "tests/distributions/contracts.py", root / "test_contracts.py"
    )
    return case, python, root, env
