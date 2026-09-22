"""Initialize editable endpoint inventories and generate typed catalogues."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import tomllib
from collections.abc import Generator, Sequence

from roboz.endpoints._inventory_codec import (
    MARKER,
    bundled_inventory,
    identifier,
    read_json,
    render_json,
)
from roboz.endpoints._inventory_codegen import render_module


FALLBACK_INVENTORY_PATH = Path("model_catalogue/models.json")
DEFAULT_MODULE_NAME = "providers.py"


def _output_path(path: Path, *, module: bool = False) -> Path:
    """Resolve a project output while rejecting symlinks and package modifications."""
    if path.is_symlink():
        raise ValueError(f"{path}: output must not be a symbolic link")
    path = path.resolve()
    if path.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError(
            f"{path}: choose a project output outside the installed package"
        )
    if path.exists() and not path.is_file():
        raise ValueError(f"{path}: output must be a regular file")
    if module:
        _validate_module_name(path)
    return path


def _validate_module_name(path: Path) -> None:
    if path.suffix != ".py":
        raise ValueError(f"{path}: generated module must have a .py extension")
    identifier(path.stem, str(path))
    if path.stem in {"roboz", "typing"}:
        raise ValueError(f"{path}: module name would hide a required package")


def _distinct(source: Path, output: Path) -> None:
    if source.resolve() == output or (
        source.exists() and output.exists() and source.samefile(output)
    ):
        raise ValueError(f"{output}: input and output must be different files")


def _module_output(path: Path, output: Path | None) -> Path:
    output = _output_path(output or path.parent / DEFAULT_MODULE_NAME, module=True)
    _distinct(path, output)
    return output


def _default_inventory_path() -> Path:
    """Place the catalogue inside the current project's import package."""
    try:
        document = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        project_name = document["project"]["name"]
    except (KeyError, OSError, tomllib.TOMLDecodeError):
        return FALLBACK_INVENTORY_PATH
    if not isinstance(project_name, str):
        return FALLBACK_INVENTORY_PATH
    package_name = re.sub(r"[-_.]+", "_", project_name)
    for package in (Path("src") / package_name, Path(package_name)):
        if (package / "__init__.py").is_file():
            return package / "model_catalogue/models.json"
    return FALLBACK_INVENTORY_PATH


def _prepare_default_package(path: Path) -> None:
    """Create the default import package without replacing existing files."""
    package = path.parent
    if package.is_symlink():
        raise ValueError(f"{package}: default catalogue must not be a symbolic link")
    package.mkdir(parents=True, exist_ok=True)
    init = package / "__init__.py"
    if init.is_symlink() or (init.exists() and not init.is_file()):
        raise ValueError(f"{init}: package marker must be a regular file")
    if not init.exists():
        init.touch()


def _advance_module_timestamp(staged: Path, output: Path) -> None:
    """Invalidate timestamp-based bytecode even for equal-length rapid updates."""
    if output.suffix != ".py" or not output.exists():
        return
    # Timestamp-based pycs compare whole seconds and size.
    timestamp = max(int(time.time()), int(output.stat().st_mtime) + 1)
    os.utime(staged, (timestamp, timestamp))


@contextmanager
def _stage(path: Path, text: str) -> Generator[Path]:
    """Prepare a complete same-directory replacement and remove it after use."""
    file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    staged = Path(file.name)
    try:
        with file:
            file.write(text)
        _advance_module_timestamp(staged, path)
        yield staged
    finally:
        staged.unlink(missing_ok=True)


def _publish_new(staged: Path, output: Path) -> None:
    try:
        os.link(staged, output)
    except FileExistsError as error:
        raise ValueError(f"{output}: already exists") from error


def _write_new(output: Path, text: str) -> None:
    with _stage(output, text) as staged:
        _publish_new(staged, output)


def _write_generated(output: Path, text: str) -> None:
    replace = output.exists()
    if replace:
        with output.open(encoding="utf-8") as file:
            marker = file.readline().rstrip("\n")
        if marker != MARKER:
            raise ValueError(f"{output}: refusing to replace an unrelated Python file")
    with _stage(output, text) as staged:
        if replace:
            os.replace(staged, output)
        else:
            _publish_new(staged, output)


def _init(path: Path, *, prepare_package: bool) -> int:
    output = _output_path(path)
    if output.exists():
        raise ValueError(f"{output}: already exists")
    text = render_json(bundled_inventory())
    if prepare_package:
        _prepare_default_package(path)
    _write_new(output, text)
    print(f"Initialized editable inventory: {output}")
    return 0


def _generate(path: Path, output: Path) -> int:
    data = read_json(path)
    _write_generated(output, render_module(data))
    print(f"Generated typed catalogue: {output}")
    print("Restart running applications to use this snapshot.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roboz.endpoints",
        description="Create typed project endpoint catalogues from editable JSON.",
    )
    groups = parser.add_subparsers(required=True)
    inventory = groups.add_parser(
        "inventory",
        help="Initialize JSON and generate a typed endpoint catalogue",
        description=(
            "Initialize an editable model inventory from RoboZ examples, then "
            "generate an importable Python catalogue with editor autocomplete."
        ),
        epilog=(
            "Typical workflow:\n"
            "  python -m roboz.endpoints inventory init\n"
            "  # Edit models.json.\n"
            "  python -m roboz.endpoints inventory generate\n\n"
            "To restore the bundled examples, delete models.json, then run init "
            "and generate again."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = inventory.add_subparsers(dest="command", required=True)
    init_command = commands.add_parser(
        "init",
        help="Create editable JSON from the bundled examples",
        description=(
            "Create an editable JSON inventory from the bundled provider and model "
            "examples. Existing JSON is never replaced; delete it first when "
            "restoring the bundled examples."
        ),
    )
    generate_command = commands.add_parser(
        "generate",
        help="Generate a typed Python catalogue from editable JSON",
        description=(
            "Validate the editable JSON and generate an importable Python module. "
            "Only an existing RoboZ-generated module may be replaced."
        ),
    )
    for command in (init_command, generate_command):
        command.add_argument(
            "--path",
            type=Path,
            help="Editable JSON path (default: model_catalogue inside project package)",
        )
    generate_command.add_argument(
        "--output",
        type=Path,
        help="Generated .py module (default: providers.py beside JSON)",
    )
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    path = args.path or _default_inventory_path()
    if args.command == "init":
        return _init(path, prepare_package=args.path is None)
    return _generate(path, _module_output(path, args.output))


def main(argv: Sequence[str] | None = None) -> int:
    """Run inventory commands, reporting expected failures without a traceback."""
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
