"""Build pinned RoboSprawl and test it with candidate library wheels outside sources."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--reports", type=Path, default=ROOT / "reports/downstream")
    args = parser.parse_args()
    source, dist = args.source.resolve(), args.dist.resolve()
    args.reports.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    with tempfile.TemporaryDirectory(prefix="roboz-downstream-") as directory:
        root = Path(directory)
        env["ROBOSPRAWL_ROOT"] = str(root)
        python = root / "venv/bin/python"

        def run(*command: str) -> None:
            subprocess.run(command, cwd=root, env=env, check=True)

        run(
            "uv",
            "build",
            "--no-sources",
            "--wheel",
            "--out-dir",
            str(root / "dist"),
            str(source),
        )
        run("uv", "venv", "--seed", "--python", sys.executable, str(root / "venv"))
        wheels = [
            next(dist.glob(pattern))
            for pattern in ("roboz-*.whl", "roboz_shed-*.whl", "roboz_openai-*.whl")
        ]
        wheels.extend((root / "dist").glob("*.whl"))
        run(
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            *(str(p) for p in wheels),
            "pytest",
        )
        run(str(python), "-I", "-m", "pip", "check")
        config = json.loads((source / "hub.config.json").read_text())
        config["sandbox"]["root"] = "hub_data"
        config["logging"]["file"]["path"] = "technical_logs/backend.jsonl"
        (root / "hub.config.json").write_text(json.dumps(config))
        shutil.copyfile(
            source / "tests/unit/test_port_composition.py", root / "test_composition.py"
        )
        shutil.copyfile(ROOT / "scripts/downstream_smoke.py", root / "smoke.py")
        try:
            run(
                str(python),
                "-I",
                "-m",
                "pytest",
                "--noconftest",
                "-q",
                str(root / "test_composition.py"),
                "--junitxml=" + str(args.reports.resolve() / "composition.xml"),
            )
            run(str(python), "-I", str(root / "smoke.py"))
        finally:
            if (root / "backend.log").exists():
                shutil.copyfile(root / "backend.log", args.reports / "backend.log")


if __name__ == "__main__":
    main()
