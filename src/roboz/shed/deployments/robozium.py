"""Robozium's persistent orchestrator and Librarian agent recipe."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from roboz.deployment import (
    Capability,
    DeployableAgent,
    SkillLabel,
    SkillLoading,
    ToolLabel,
)
from roboz.llm import EndpointLike, LLMEndpoint, LLMEndpointRoute
from roboz.shed.agents import librarian, orchestrator
from roboz.shed.capabilities import Compactification, Email, SafeScripts
from roboz.shed.sandbox import Sandbox
from roboz.shed.skills import robozium as robozium_skill
from roboz.shed.tools.email import EmailService


def robozium(
    sandbox: Sandbox,
    /,
    *,
    endpoint_getter: Callable[[], LLMEndpoint],
    memory_endpoint: EndpointLike,
    email_service: EmailService,
    specialists: Sequence[DeployableAgent] = (),
    scripts_dir: Path | None = None,
    script_socket: Path | None = None,
) -> DeployableAgent:
    """Define the persistent orchestrator and its fixed Librarian maintenance.

    The orchestrator owns its standard capabilities. SafeScripts and email are
    selectable; callers may attach local capabilities and choose a selection
    before calling ``build``. Email uses the supplied service. Scripts use
    the read-only safe-scripts directory unless a directory or socket is supplied.
    """
    sandbox = replace(sandbox)
    project_slug = sandbox.scope
    if project_slug is None:
        raise ValueError("sandbox scope is not configured; call configure_scope()")
    sandbox.project_dir()

    root = orchestrator(
        sandbox,
        agent_endpoint=LLMEndpointRoute(endpoint_getter),
        nested_agents=tuple(specialists),
    )
    if scripts_dir is None and script_socket is None:
        scripts_dir = sandbox.readonly_dir / "safe-scripts"
    root.add_capabilities(
        Capability(
            label=SkillLabel("robozium", loading=SkillLoading.AUTOMATIC),
            value=robozium_skill,
        ),
        Compactification(threshold_percent=60.0),
        Email(
            label=SkillLabel("email", selectable=True, loading=SkillLoading.AUTOMATIC),
            service=email_service,
        ),
        SafeScripts(
            label=ToolLabel("safe_scripts", selectable=True),
            scripts_dir=scripts_dir,
            socket_path=script_socket,
        ),
    )
    watched_agent_names = root.agent_names(include_background=False)
    root.add_background_agents(
        librarian(sandbox, watched_agent_names, agent_endpoint=memory_endpoint)
    )
    context = (
        "## Project context\n"
        f"File tool base: {sandbox.resolved_root}\n"
        f"Project: {project_slug}\n"
        f"Writable project directory: {sandbox.project_dir()}\n"
        f"Read-only directory: {sandbox.readonly_dir}\n"
        f"Shared directory: {sandbox.shared_dir}\n"
        f"Conversation logs: {sandbox.project_logs_dir()}\n"
        f"Snapshots: {sandbox.project_snapshots_dir()}\n"
        f"Memory: {sandbox.project_memory_dir()}"
    )
    root.set_initial_messages((sandbox.project_memory_dir(), context))
    return root


__all__ = ["robozium"]
