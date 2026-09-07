"""Installed assistant smoke test and optional real-model playground."""

import argparse
import html
from pathlib import Path
from uuid import uuid4

from roboshed.workspace import Project, Workspace
from roboz import Role, Tool
from roboz.deployment import AgentCapability, Capability
from roboz.llm import EndpointLike, MockLLMEndpoint, resolve_endpoint
from roboz.runtime import CliSink, EventPipe

from .assistant import build_assistant
from .workspace import WorkspacePermissions


def main(argv: list[str] | None = None) -> None:
    """Run the installed mock smoke test or optional real-provider demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--mock", action="store_true", help="No credentials or network calls"
    )
    mode.add_argument("--provider", choices=["openrouter", "openai"])
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--data-path", required=True, type=Path)
    parser.add_argument("--model", help="Provider model ID (required for real runs)")
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        help="Model context limit (required for real runs)",
    )
    parser.add_argument("--prompt", help="Task for a real model")
    parser.add_argument(
        "--proton", action="store_true", help="Enable configured Proton email tools"
    )
    parser.add_argument(
        "--signature-file", type=Path, help="Optional plain-text email signature"
    )
    args = parser.parse_args(argv)
    if args.mock and args.proton:
        parser.error("--proton requires a real provider run")
    if args.signature_file and not args.proton:
        parser.error("--signature-file requires --proton")
    if not args.mock and (
        not args.model or not args.max_context_tokens or not args.prompt
    ):
        parser.error("real runs require --model, --max-context-tokens, and --prompt")

    endpoint: EndpointLike
    filename = f"roboz-demo-{uuid4().hex[:12]}.txt"
    if args.mock:
        endpoint = MockLLMEndpoint(
            [
                {
                    "action": "apply_patch",
                    "rationale": "Create the demo file",
                    "path": filename,
                    "old_string": "",
                    "new_string": "Hello from Roboz!\n",
                    "replace_all": False,
                },
                {
                    "action": "run_file_command",
                    "rationale": "Read the demo file",
                    "chain": "and",
                    "file_commands": [{"command": "cat", "argv": [filename]}],
                },
                {
                    "action": "stop",
                    "rationale": "Finished the demonstration",
                    "value": "Demo complete.",
                },
            ]
        )
    else:
        try:
            from roboz_openai import openai_endpoint, openrouter_endpoint
        except ModuleNotFoundError as error:
            if error.name != "roboz_openai":
                raise
            parser.error("Install roboz-openai to use a real provider")
        constructor = (
            openrouter_endpoint if args.provider == "openrouter" else openai_endpoint
        )
        endpoint = constructor(
            model=args.model, max_context_tokens=args.max_context_tokens
        )

    project = Project(
        Workspace(args.workspace), "assistant", logs_dir=args.data_path.resolve()
    )
    workspace = WorkspacePermissions.local(project.root)
    capabilities: list[AgentCapability] = []
    if args.proton:
        try:
            from roboz_proton_bridge import (
                ProtonBridgeEmailService,
                ProtonBridgeSettings,
            )
        except ModuleNotFoundError as error:
            if error.name != "roboz_proton_bridge":
                raise
            parser.error("Install roboz-proton-bridge to enable email")
        from .skills import email_skill
        from .tools.email import EmailProviderError, EmailSignature, get_work_with_email

        try:
            settings = ProtonBridgeSettings.from_environment()
        except EmailProviderError as error:
            # A settings-validation cause may contain the supplied environment
            # values. Show the adapter's safe message, not its traceback.
            parser.error(str(error))

        signature = (
            args.signature_file.read_text(encoding="utf-8")
            if args.signature_file
            else ""
        )
        service = ProtonBridgeEmailService(
            settings=settings,
            signature=EmailSignature(plain_text=signature, html=html.escape(signature)),
        )

        def email_tools(pipe: EventPipe) -> list[Tool]:
            return get_work_with_email(
                service=service,
                base=workspace.base,
                default_verdict=workspace.default_verdict,
                allow_rules=list(workspace.allow),
                deny_rules=list(workspace.deny),
                ask_rules=list(workspace.ask),
                pipe=pipe,
                is_cancelled=lambda: pipe.cancelled,
            )

        class EmailCapability:
            def build(self, pipe, agent_endpoint):
                return Capability(tools=tuple(email_tools(pipe)), skills=(email_skill,))

        capabilities.append(EmailCapability())

    # Resolve once so missing credentials fail before starting an interactive run.
    try:
        materialized = resolve_endpoint(endpoint)
    except ValueError as error:
        parser.error(str(error))
    try:
        workspace.base.mkdir(parents=True, exist_ok=True)
        data_path = args.data_path.resolve()
        agent = build_assistant(
            endpoint=materialized,
            project=project,
            permissions=workspace,
            capabilities=capabilities,
            initial_messages=[
                args.prompt or "Create and read the demonstration file, then stop."
            ],
            event_sinks=[
                CliSink(types_to_print={Role.ERROR}),
            ],
        )
        result, messages = agent.invoke()
        if args.mock:
            output = workspace.base / filename
            if output.read_text(encoding="utf-8") != "Hello from Roboz!\n":
                raise RuntimeError("Demo file verification failed")
            if not any("Hello from Roboz!" in message.content for message in messages):
                raise RuntimeError("Demo tool output was not observed")
            print(f"Verified demo file: {output}")
        print(result.value or "Run finished.")
        print(f"Conversation data: {data_path}")
    finally:
        if not isinstance(materialized, MockLLMEndpoint):
            materialized.client.close()


if __name__ == "__main__":
    main()
