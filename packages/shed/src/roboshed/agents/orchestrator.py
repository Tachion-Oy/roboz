"""Opinionated persistent collaboration, independent of providers and hosts."""

from collections.abc import Sequence
from pathlib import Path

from roboz.deployment import AgentCapability, AgentDefinition, Capability, SubAgentSpec
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
    *,
    agent_endpoint: EndpointLike,
    name: str = "orchestrator",
    subagents: Sequence[SubAgentSpec] = (),
    capabilities: Sequence[AgentCapability] = (),
    instructions: str = "",
    initial_messages: Sequence[Path | str] = (),
    interaction_mode: Output | None = Output.CLI,
) -> AgentDefinition:
    """Configure a persistent orchestrator with caller-selected capabilities.

    Capabilities are the only extension input. The preset always supplies the
    role prompt and stop tool; the runtime supplies user interaction. It selects
    no provider, tool integration, filesystem permission, or memory location.
    Interaction defaults to CLI; hosts select their channel explicitly.
    Additional instructions describe the deployment without changing core prompts.
    """
    return AgentDefinition(
        name=name,
        agent_endpoint=agent_endpoint,
        description="Coordinates ongoing user goals and specialist agents.",
        system_prompt=ORCHESTRATOR_PROMPT
        + ("\n\n" + instructions if instructions else ""),
        capabilities=(Capability(tools=(stop,)), *capabilities),
        subagents=tuple(subagents),
        initial_messages=tuple(initial_messages),
        interaction_mode=interaction_mode,
    )
