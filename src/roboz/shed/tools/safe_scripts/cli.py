"""Command-line entrypoint for the Linux host script service."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .client import ScriptSocketDependency
from .protocol import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_S,
    ExecutionPolicy,
    require_linux_transport,
)
from .server import serve_scripts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roboz.shed.tools.safe_scripts",
        description="Serve trusted Bash scripts on a Linux host or check its socket.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the host script service")
    serve.add_argument("--socket", type=Path, required=True)
    serve.add_argument("--scripts", type=Path, required=True)
    serve.add_argument("--cwd", type=Path, required=True)
    serve.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    serve.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    serve.add_argument(
        "--allow-env",
        metavar="NAME",
        action="append",
        default=[],
        help="Pass this host environment variable to scripts; repeat as needed",
    )
    check = commands.add_parser("check", help="Check the service handshake")
    check.add_argument("--socket", type=Path, required=True)
    return parser


def _linux_available() -> bool:
    try:
        require_linux_transport()
    except ValueError as error:
        print(f"Host script service: {error}", file=sys.stderr)
        return False
    return True


def _serve(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        policy = ExecutionPolicy(
            args.timeout_s, args.max_output_bytes, tuple(args.allow_env)
        )
    except ValueError as error:
        parser.error(str(error))
    if not _linux_available():
        return 1
    try:
        serve_scripts(
            socket_path=args.socket,
            scripts_dir=args.scripts,
            cwd=args.cwd,
            timeout_s=policy.timeout_s,
            max_output_bytes=policy.max_output_bytes,
            env_allowlist=policy.env_allowlist,
        )
    except (OSError, ValueError) as error:
        print(f"Host script service: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the service or a greeting-only health check and return an exit status."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args, parser)
    if not _linux_available():
        return 1
    if ScriptSocketDependency(args.socket).check():
        return 0
    print("Host script service is unavailable or incompatible", file=sys.stderr)
    return 1
