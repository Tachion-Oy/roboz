"""Export, import, and reset project inventories without SDKs or credentials."""

import argparse
from contextlib import ExitStack, contextmanager
from importlib.metadata import version
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
    read_module,
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
    """Prepare a complete same-directory replacement and clean up temporary files."""
    with ExitStack() as cleanup:
        file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        )
        staged = Path(file.name)
        cleanup.callback(staged.unlink, missing_ok=True)
        with file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        _advance_module_timestamp(staged, path)
        yield staged


def _publish(staged: Path, output: Path, *, force: bool) -> None:
    """Publish atomically, with an exclusive creation when replacement is disabled."""
    if force:
        os.replace(staged, output)
    else:
        try:
            os.link(staged, output)
        except FileExistsError as error:
            raise ValueError(
                f"{output}: already exists; use --force to replace it"
            ) from error


def _write_output(output: Path, text: str, *, force: bool) -> None:
    with _stage(output, text) as staged:
        _publish(staged, output, force=force)


def _check_reset_module(output: Path) -> None:
    """Allow absent or generated modules, but never reset unrelated Python files."""
    if not output.exists():
        return
    with output.open(encoding="utf-8") as file:
        marker = file.readline().rstrip("\n")
    if marker != MARKER:
        raise ValueError(f"{output}: refusing to reset an unrelated Python file")


def _confirm_reset(path: Path, output: Path) -> bool:
    """Require explicit consent; an empty response or EOF never authorizes reset."""
    print(f"Restore RoboZ {version('roboz')} bundled inventory:")
    print(f"  Editable JSON: {path}\n  Generated module: {output}")
    print("Custom providers and models in these files will be discarded.")
    try:
        answer = input(
            "Discard custom inventory changes and restore installed defaults? [y/N] "
        )
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _restore_files(files: Sequence[tuple[Path, str]]) -> None:
    """Stage every file before replacing any; report partial publication failures."""
    changed: list[Path] = []
    try:
        with ExitStack() as stack:
            replacements = [
                (stack.enter_context(_stage(path, text)), path) for path, text in files
            ]
            for staged, target in replacements:
                _publish(staged, target, force=True)
                changed.append(target)
    except OSError as error:
        updated = ", ".join(str(target) for target in changed) or "none"
        raise ValueError(
            f"Reset incomplete. Changed files: {updated}. Re-run reset. {error}"
        ) from error


def _reset(path: Path, output: Path) -> int:
    """Confirm restoration of both project files from the installed inventory."""
    if not path.exists() and not output.exists():
        print("Nothing to reset: neither inventory file exists.")
        return 0
    _check_reset_module(output)
    inventory = bundled_inventory()
    files = ((path, render_json(inventory)), (output, render_module(inventory)))
    if not _confirm_reset(path, output):
        print("Reset cancelled; no files changed.")
        return 1
    _restore_files(files)
    print(
        "Restored both files to installed defaults. Restart applications to use them."
    )
    return 0


def _export(path: Path, *, from_module: Path | None, force: bool) -> int:
    output = _output_path(path)
    if from_module is not None:
        _distinct(from_module, output)
    data = read_module(from_module) if from_module is not None else bundled_inventory()
    _write_output(output, render_json(data), force=force)
    print(f"Exported inventory: {output}")
    return 0


def _import(path: Path, output: Path, *, force: bool) -> int:
    data = read_json(path)
    _write_output(output, render_module(data), force=force)
    print(f"Generated inventory: {output}")
    if data:
        print("Import the generated module through your application's package path.")
    print("Restart applications to use this snapshot.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roboz.endpoints", description=__doc__
    )
    groups = parser.add_subparsers(dest="group", required=True)
    inventory = groups.add_parser("inventory", help="Manage project model snapshots")
    commands = inventory.add_subparsers(dest="command", required=True)
    export_command = commands.add_parser("export")
    import_command = commands.add_parser("import")
    reset_command = commands.add_parser("reset")
    for command in (export_command, import_command, reset_command):
        command.add_argument(
            "--path",
            type=Path,
            help="Editable JSON path (default: model_catalogue inside project package)",
        )
    export_command.add_argument(
        "--from-module",
        type=Path,
        help="Read a generated snapshot instead of bundled models",
    )
    for command in (import_command, reset_command):
        command.add_argument(
            "--output",
            type=Path,
            help="Generated .py module (default: providers.py beside JSON)",
        )
    for command in (export_command, import_command):
        command.add_argument(
            "--force", action="store_true", help="Replace an existing output"
        )
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    path = args.path or _default_inventory_path()
    if args.command == "export":
        if args.path is None:
            _prepare_default_package(path)
        return _export(path, from_module=args.from_module, force=args.force)
    output = _module_output(path, args.output)
    if args.command == "reset":
        return _reset(_output_path(path), output)
    return _import(path, output, force=args.force)


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
