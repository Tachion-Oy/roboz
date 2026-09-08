"""Check release preparation and select or build one distribution."""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import re
import subprocess
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "roboz": ROOT,
    "roboshed": ROOT / "packages/shed",
    "roboz-endpoints": ROOT / "packages/endpoints",
    "roboz-proton-bridge": ROOT / "packages/proton-bridge",
}


def project_version(name: str) -> str:
    project = tomllib.loads((PROJECTS[name] / "pyproject.toml").read_text())["project"]
    version = project["version"]
    # Keep filenames and GitHub outputs single-line; the build tools validate PEP 440.
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9][A-Za-z0-9.!+_-]*", version
    ):
        raise ValueError(f"Invalid release version for {name}: {version!r}")
    return version


def check_preparation(name: str, *, today: date | None = None) -> None:
    version = project_version(name)
    changelog = PROJECTS[name] / "CHANGELOG.md"
    text = re.sub(r"<!--.*?-->", "", changelog.read_text(), flags=re.DOTALL)
    sections = re.split(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    entries = list(zip(sections[1::2], sections[2::2]))

    def has_notes(body: str) -> bool:
        return any(
            line.strip(" \t-*+") and not line.lstrip().startswith("#")
            for line in body.splitlines()
        )

    if not entries or entries[0][0] != "Unreleased":
        raise ValueError(f"{changelog}: start with an empty '## Unreleased' section")
    if sum(title == "Unreleased" for title, _ in entries) != 1 or has_notes(
        entries[0][1]
    ):
        raise ValueError(
            f"{changelog}: move Unreleased notes into the selected release"
        )
    pattern = rf"\[?{re.escape(version)}\]? - (\d{{4}}-\d{{2}}-\d{{2}})"
    if len(entries) < 2 or not (match := re.fullmatch(pattern, entries[1][0])):
        raise ValueError(
            f"{changelog}: expected '## {version} - YYYY-MM-DD' after Unreleased"
        )
    release_date = date.fromisoformat(match[1])
    if release_date > (today or datetime.now(timezone.utc).date()):
        raise ValueError(f"{changelog}: release date must not be in the future")
    if not has_notes(entries[1][1]):
        raise ValueError(f"{changelog}: release {version} needs nonempty notes")
    if any(
        re.match(rf"\[?{re.escape(version)}(?:\]|\s|$)", title)
        for title, _ in entries[2:]
    ):
        raise ValueError(f"{changelog}: duplicate release entry for {version}")


def resolve_tag(tag: str) -> str:
    name, separator, version = tag.rpartition("-v")
    if not separator or name not in PROJECTS:
        raise ValueError("Expected <package>-v<version> for a known workspace package")
    declared = project_version(name)
    if version != declared:
        raise ValueError(
            f"Tag version {version!r} does not match {name} version {declared!r}"
        )
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    parser.add_argument(
        "--package", choices=PROJECTS, help="Select a tagless rehearsal"
    )
    parser.add_argument(
        "--github-output", type=Path, help="Append validated job outputs"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check version and changelog without building",
    )
    parser.add_argument(
        "--from-dist",
        type=Path,
        help="Select already verified artifacts; do not rebuild",
    )
    args = parser.parse_args()
    if bool(args.tag) == bool(args.package):
        parser.error("Supply either a release tag or --package")
    name = resolve_tag(args.tag) if args.tag else args.package
    version = project_version(name)
    if args.check:
        check_preparation(name)
    if not args.check:
        output = ROOT / "dist" / "release"
        if output.exists() and any(output.iterdir()):
            parser.error(f"Release output must be empty: {output}")
        if args.from_dist:
            stem = f"{name.replace('-', '_')}-{version}"
            artifacts = [
                args.from_dist / f"{stem}{suffix}"
                for suffix in ("-py3-none-any.whl", ".tar.gz")
            ]
            if not all(path.is_file() for path in artifacts):
                parser.error("Both verified wheel and source distribution are required")
            output.mkdir(parents=True, exist_ok=True)
            for artifact in artifacts:
                shutil.copyfile(artifact, output / artifact.name)
        else:
            subprocess.run(
                ["uv", "build", "--package", name, "--out-dir", str(output)],
                cwd=ROOT,
                check=True,
            )
    if args.github_output:
        with args.github_output.open("a") as output_file:
            output_file.write(
                f"package={name}\nversion={version}\ntag={name}-v{version}\n"
            )
    print(name)


if __name__ == "__main__":
    main()
