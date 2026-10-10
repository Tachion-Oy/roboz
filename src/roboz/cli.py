"""Create local capabilities, manage catalogues and secrets, and serve host scripts."""

import argparse
from getpass import getpass
from importlib.machinery import PathFinder
from importlib.resources import files
from importlib.util import find_spec
from keyword import iskeyword
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import tomllib
from collections.abc import Sequence
from string import Template


FALLBACK_INVENTORY_PATH = Path("model_catalogue/models.json")
DEFAULT_MODULE_NAME = "providers.py"


def _project_output(path: Path) -> Path:
    """Resolve a project output while rejecting symlinks and package modifications."""
    if path.is_symlink():
        raise ValueError(f"{path}: output must not be a symbolic link")
    path = path.resolve()
    if path.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError(
            f"{path}: choose a project output outside the installed package"
        )
    return path


def _output_path(path: Path, *, module: bool = False) -> Path:
    path = _project_output(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"{path}: output must be a regular file")
    if module:
        _validate_module_name(path)
    return path


def _validate_module_name(path: Path) -> None:
    from roboz.endpoints._inventory_codec import identifier

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


def _write_output(output: Path, text: str, *, replace: bool = False) -> None:
    """Publish a complete file atomically and clean up temporary data on failure."""
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=f".{output.name}.") as temporary:
        staged = Path(temporary) / output.name
        staged.touch(mode=0o600)
        staged.write_text(text, encoding="utf-8", newline="\n")
        _advance_module_timestamp(staged, output)
        if replace:
            os.replace(staged, output)
        else:
            try:
                os.link(staged, output)
            except FileExistsError as error:
                raise ValueError(f"{output}: already exists") from error


def _write_generated(output: Path, text: str) -> None:
    from roboz.endpoints._inventory_codec import MARKER

    replace = output.exists()
    if replace:
        with output.open(encoding="utf-8") as file:
            marker = file.readline().rstrip("\n")
        if marker != MARKER:
            raise ValueError(f"{output}: refusing to replace an unrelated Python file")
    _write_output(output, text, replace=replace)


def _init_inventory(path: Path, *, prepare_package: bool) -> int:
    from roboz.endpoints._inventory_codec import bundled_inventory, render_json

    output = _output_path(path)
    if output.exists():
        raise ValueError(f"{output}: already exists")
    text = render_json(bundled_inventory())
    if prepare_package:
        _prepare_default_package(path)
    _write_output(output, text)
    print(f"Initialized editable inventory: {output}")
    return 0


def _generate_inventory(path: Path, output: Path) -> int:
    from roboz.endpoints._inventory_codec import read_json
    from roboz.endpoints._inventory_codegen import render_module

    data = read_json(path)
    _write_generated(output, render_module(data))
    print(f"Generated typed catalogue: {output}")
    print("Restart running applications to use this snapshot.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roboz",
        description=(
            "Create local tools and skills, manage typed project endpoint catalogues "
            "and encrypted dotenv secrets, and serve trusted host scripts."
        ),
    )
    groups = parser.add_subparsers(dest="group", required=True)
    environment = groups.add_parser("env", help="Manage encrypted dotenv secrets")
    env_commands = environment.add_subparsers(dest="command", required=True)
    encrypt_command = env_commands.add_parser(
        "encrypt", help="Encrypt selected variables in a dotenv file"
    )
    encrypt_command.add_argument(
        "--path", type=Path, default=Path(".env"),
        help="Plaintext source file (default: .env; output adds .encrypt)"
    )
    encrypt_command.add_argument("--secret", action="append", required=True, help="Base name to encrypt (repeat for each secret)")
    inventory = groups.add_parser(
        "inventory",
        help="Initialize JSON and generate a typed endpoint catalogue",
        description=(
            "Initialize an editable model inventory from RoboZ examples, then "
            "generate an importable Python catalogue with editor autocomplete."
        ),
        epilog=(
            "Typical workflow:\n"
            "  roboz inventory init\n"
            "  # Edit models.json.\n"
            "  roboz inventory generate\n\n"
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
    scripts = groups.add_parser("scripts", help="Serve trusted Bash scripts on Linux")
    script_commands = scripts.add_subparsers(dest="command", required=True)
    serve = script_commands.add_parser("serve", help="Run the host script service")
    serve.add_argument("--socket", type=Path, required=True)
    serve.add_argument("--scripts", type=Path, required=True)
    serve.add_argument("--cwd", type=Path, required=True)
    serve.add_argument("--timeout-s", type=float)
    serve.add_argument("--max-output-bytes", type=int)
    serve.add_argument(
        "--allow-env", metavar="NAME", action="append", default=[],
        help="Pass this host environment variable to scripts; repeat as needed",
    )
    check = script_commands.add_parser("check", help="Check the service handshake")
    check.add_argument("--socket", type=Path, required=True)
    for kind, name in (("tool", "simpsons_quotes"), ("skill", "simpsons_quotes_skill")):
        group = groups.add_parser(kind, help=f"Create an editable {kind} capability")
        commands = group.add_subparsers(dest="command", required=True)
        command = commands.add_parser(
            "init", help=f"Create a Simpsons quote {kind} package",
            description=(
                "Create an editable capability with its own tool and requirements. "
                "Existing destinations are never replaced."
            ),
        )
        command.add_argument(
            "--path", type=Path, default=Path("local") / f"{kind}s" / name,
            help=f"Destination package (default: local/{kind}s/{name})",
        )
    return parser


def _encrypt(path: Path, secret_names: list[str]) -> int:
    from roboz.endpoints.env import _PASSWORD_ENV, encrypt_env

    password = None
    if _PASSWORD_ENV not in os.environ:
        password = getpass("Encryption password: ")
        if password != getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
    output = encrypt_env(path, secret_names=secret_names, password=password)
    print(f"Encrypted secrets: {output}")
    return 0


def _scripts(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from roboz.shed.tools.safe_scripts.client import ScriptSocketDependency
    from roboz.shed.tools.safe_scripts.protocol import (
        ExecutionPolicy,
        require_linux_transport,
    )
    from roboz.shed.tools.safe_scripts.server import serve_scripts

    if args.command == "check":
        require_linux_transport()
        if ScriptSocketDependency(args.socket).check() is not None:
            raise ValueError("Host script service is unavailable or incompatible")
        return 0
    defaults = ExecutionPolicy()
    try:
        policy = ExecutionPolicy(
            args.timeout_s if args.timeout_s is not None else defaults.timeout_s,
            args.max_output_bytes if args.max_output_bytes is not None else defaults.max_output_bytes,
            tuple(args.allow_env),
        )
    except ValueError as error:
        parser.error(str(error))
    require_linux_transport()
    serve_scripts(
        socket_path=args.socket, scripts_dir=args.scripts, cwd=args.cwd,
        timeout_s=policy.timeout_s, max_output_bytes=policy.max_output_bytes,
        env_allowlist=policy.env_allowlist,
    )
    return 0


def _init_capability(kind: str, path: Path) -> int:
    from roboz._naming import validate_public_name

    output = _project_output(path)
    name = validate_public_name(output.name, kind="package")
    if iskeyword(name) or name == "roboz":
        raise ValueError(f"{path}: choose a non-keyword package name other than roboz")
    if output.exists():
        raise ValueError(f"{path}: already exists")
    import_roots = {Path.cwd().resolve(), *(Path(entry).resolve() for entry in sys.path if entry)}
    if output.parent in import_roots and (
        find_spec(name) is not None
        or PathFinder.find_spec(name, [str(output.parent)]) is not None
    ):
        raise ValueError(f"{path}: {name!r} conflicts with an importable top-level module")
    templates = files("roboz").joinpath("cli_templates")
    sources = {
        "tool.py": "tool.py.tmpl",
        "requirements.txt": "requirements.txt.tmpl",
        "__init__.py": f"{kind}_init.py.tmpl",
    }
    rendered = {
        filename: Template(templates.joinpath(template).read_text(encoding="utf-8"))
        .substitute(name=name)
        for filename, template in sources.items()
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    created: list[Path] = []
    try:
        for filename, text in rendered.items():
            target = output / filename
            with target.open("x", encoding="utf-8", newline="\n") as file:
                created.append(target)
                file.write(text)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        output.rmdir()
        raise
    print(f"Created {kind} capability: {output}")
    print("Edit tool.py and requirements.txt; configure CAPABILITY in __init__.py.")
    return 0


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.group == "env":
        return _encrypt(args.path, args.secret)
    if args.group == "scripts":
        return _scripts(args, parser)
    if args.group in {"tool", "skill"}:
        return _init_capability(args.group, args.path)
    path = args.path or _default_inventory_path()
    if args.command == "init":
        return _init_inventory(path, prepare_package=args.path is None)
    return _generate_inventory(path, _module_output(path, args.output))


def main(argv: Sequence[str] | None = None) -> int:
    """Run RoboZ commands, reporting expected failures without a traceback."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, parser)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (EOFError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
