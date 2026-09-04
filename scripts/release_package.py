"""Validate a package-specific release tag and build only that distribution."""

import argparse
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboz-shed": ROOT / "packages/shed",
    "roboz-openai": ROOT / "packages/openai",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}


def resolve_tag(tag: str) -> str:
    name, separator, version = tag.rpartition("-v")
    if not separator or name not in PROJECTS:
        raise ValueError("Expected <package>-v<version> for a known workspace package")
    project = tomllib.loads((PROJECTS[name] / "pyproject.toml").read_text())["project"]
    if version != project["version"]:
        raise ValueError(
            f"Tag version {version!r} does not match {name} version {project['version']!r}"
        )
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument(
        "--check", action="store_true", help="Validate without building"
    )
    args = parser.parse_args()
    name = resolve_tag(args.tag)
    if not args.check:
        output = ROOT / "dist" / "release"
        if output.exists() and any(output.iterdir()):
            parser.error(f"Release output must be empty: {output}")
        subprocess.run(
            ["uv", "build", "--package", name, "--out-dir", str(output)],
            cwd=ROOT,
            check=True,
        )
    print(name)


if __name__ == "__main__":
    main()
