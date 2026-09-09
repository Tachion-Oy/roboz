"""Validate a package tag and select its already-built release artifacts."""

import argparse
from pathlib import Path
import re
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboshed": ROOT / "packages/shed",
    "roboz-endpoints": ROOT / "packages/endpoints",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}


def project_metadata(name: str) -> dict:
    return tomllib.loads((PROJECTS[name] / "pyproject.toml").read_text())["project"]


def project_version(name: str) -> str:
    version = project_metadata(name)["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9][A-Za-z0-9.!+_-]*", version
    ):
        raise ValueError(f"Invalid release version for {name}: {version!r}")
    return version


def resolve_tag(tag: str) -> str:
    name, separator, version = tag.rpartition("-v")
    if not separator or name not in PROJECTS:
        raise ValueError("Expected <package>-v<version> for a known workspace package")
    if version != project_version(name):
        raise ValueError(f"Tag version {version!r} does not match {name}")
    return name


def artifacts(dist: Path, name: str) -> tuple[Path, Path]:
    stem = f"{name.replace('-', '_')}-{project_version(name)}"
    paths = (dist / f"{stem}-py3-none-any.whl", dist / f"{stem}.tar.gz")
    if not all(path.is_file() for path in paths):
        raise ValueError(f"Missing wheel or source distribution for {name} in {dist}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="Check tag/version agreement"
    )
    mode.add_argument(
        "--from-dist", type=Path, help="Select verified artifacts without rebuilding"
    )
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    name = resolve_tag(args.tag)
    if args.from_dist:
        selected = artifacts(args.from_dist, name)
        output = ROOT / "dist/release"
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            parser.error(f"Release output must be empty: {output}")
        for artifact in selected:
            shutil.copyfile(artifact, output / artifact.name)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"package={name}\nversion={project_version(name)}\n")
    print(name)


if __name__ == "__main__":
    main()
