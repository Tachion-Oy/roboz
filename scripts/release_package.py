"""Resolve a tagged release candidate and select its verified artifacts."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboshed": ROOT / "packages/shed",
    "roboz-endpoints": ROOT / "packages/endpoints",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}
PUBLISHABLE_PROJECTS = {"roboz", "roboshed", "roboz-endpoints"}


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(error.stderr.strip() or "Git validation failed") from error
    return result.stdout.strip()


def project_metadata(name: str, *, commit: str | None = None) -> dict:
    path = PROJECTS[name] / "pyproject.toml"
    content = (
        git("show", f"{commit}:{path.relative_to(ROOT).as_posix()}")
        if commit is not None
        else path.read_text()
    )
    return tomllib.loads(content)["project"]


def project_version(name: str, *, commit: str | None = None) -> str:
    metadata = project_metadata(name, commit=commit)
    if commit is not None and metadata.get("name") != name:
        raise ValueError(f"Tagged project metadata does not name {name!r}")
    version = metadata["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9][A-Za-z0-9.!+_-]*", version
    ):
        raise ValueError(f"Invalid release version for {name}: {version!r}")
    return version


def tag_parts(tag: str) -> tuple[str, str]:
    name, separator, version = tag.rpartition("-v")
    if not separator or name not in PROJECTS:
        raise ValueError("Expected <package>-v<version> for a known workspace package")
    return name, version


def resolve_tag(tag: str) -> str:
    name, version = tag_parts(tag)
    if version != project_version(name):
        raise ValueError(f"Tag version {version!r} does not match {name}")
    return name


def resolve_candidate(tag: str, base_commit: str) -> tuple[str, str, str]:
    name, version = tag_parts(tag)
    if name not in PUBLISHABLE_PROJECTS:
        raise ValueError(f"Production publishing is not enabled for {name}")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", base_commit):
        raise ValueError("The main workflow commit must be a full commit SHA")
    ref = f"refs/tags/{tag}"
    git("check-ref-format", ref)
    try:
        commit = git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    except ValueError as error:
        raise ValueError(
            f"Release tag {tag!r} does not identify an existing commit"
        ) from error
    try:
        git("merge-base", "--is-ancestor", commit, base_commit)
    except ValueError as error:
        raise ValueError(
            "The tagged commit must be in main's history at workflow invocation"
        ) from error
    if version != project_version(name, commit=commit):
        raise ValueError(
            f"Tag version {version!r} does not match tagged {name} metadata"
        )
    return name, version, commit


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
    mode.add_argument(
        "--resolve",
        action="store_true",
        help="Resolve an existing tag merged into main",
    )
    parser.add_argument(
        "--base-commit", help="Full main commit SHA at workflow invocation"
    )
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.resolve:
        if args.base_commit is None:
            parser.error("--resolve requires --base-commit")
        name, version, commit = resolve_candidate(args.tag, args.base_commit)
        if args.github_output:
            with args.github_output.open("a") as output:
                output.write(f"package={name}\nversion={version}\nsha={commit}\n")
        print(f"{name} {version} ({commit})")
        return
    if args.base_commit is not None:
        parser.error("--base-commit requires --resolve")
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
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from error
