"""Persistent orchestrator constructor with its filesystem capabilities."""

from collections.abc import Sequence
from pathlib import Path

from roboshed.capabilities import FileCommands, FileEditing
from roboshed.sandbox import Sandbox
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import EndpointLike
from roboz.runtime import Output
from roboz.tools import stop

ORCHESTRATOR_PROMPT = """You are the user's persistent collaborator. Help them reach their
goals using the available capabilities, and delegate to suitable specialists.
Read supplied memory and apply the user's recorded preferences. Ask for missing
information when you cannot obtain it with the available tools. After completing
work, report the result and ask what the user wants to do next. Remain available
across tasks: do not end the session merely because a task is finished. Call stop
only when the user asks to end the session, including a standing instruction they
have supplied. Do not claim capabilities that are not available."""


def orchestrator(
    sandbox: Sandbox,
    *,
    agent_endpoint: EndpointLike,
    subagents: Sequence[DeployableAgent] = (),
    background_agents: Sequence[DeployableAgent] = (),
    interaction_mode: Output | None = Output.CLI,
    initial_messages: Sequence[Path | str] = (),
) -> DeployableAgent:
    """Configure the orchestrator for an already-configured sandbox."""
    agent = DeployableAgent(
        name="orchestrator",
        description="Coordinates ongoing user goals and specialist agents.",
        system_prompt=ORCHESTRATOR_PROMPT,
        default_capabilities=(
            Capability(tools=(stop,)),
            FileCommands(),
            FileEditing(),
        ),
        subagents=tuple(subagents),
        background_agents=tuple(background_agents),
    )
    agent.set_agent_endpoint(agent_endpoint)
    agent.set_interaction_mode(interaction_mode)
    agent.set_initial_messages(initial_messages)
    agent.set_attributes(permissions=sandbox.permissions())
    return agent


__all__ = ["ORCHESTRATOR_PROMPT", "orchestrator"]
