"""Configured agent graphs with filesystem boundaries and event sinks."""

from dataclasses import dataclass, field, replace

from roboshed.sandbox import Sandbox
from roboz.agent import Agent
from roboz.deployment import AgentCapability, DeployableAgent
from roboz.runtime import EventSink, default_event_sinks


@dataclass(kw_only=True)
class Deployment:
    """An agent graph and its configurable boundaries and output destinations.

    Select a scope through sandbox.configure_scope(), configure any root-only
    additional capabilities and exposed sinks, then build. Do not reconfigure
    this object concurrently. Each build captures paths and sink registrations;
    supplied capabilities, endpoints, and sinks remain caller-owned. Caller
    event_sinks follow foreground branches only. Every agent receives fresh
    persistence and optional CLI sinks.
    """

    agent: DeployableAgent
    sandbox: Sandbox
    additional_capabilities: tuple[AgentCapability, ...] = ()
    event_sinks: list[EventSink] = field(default_factory=list)
    include_cli_output: bool = False

    def build(self) -> tuple[Agent, tuple[Agent, ...]]:
        """Assemble fresh agents from the configured scope without starting work.

        Fail before runtime construction if the scope is absent or names collide.
        Background handles include descendants of both child slots; callers own
        invocation, interruption, cancellation, and shutdown through their pipes.
        Additional capabilities follow the root definition's owned capability
        sequence. Later configuration changes affect only subsequent builds.
        """
        sandbox = replace(self.sandbox)
        sandbox.project_dir()
        self.agent.agent_names()
        caller_sinks = tuple(self.event_sinks)
        include_cli = self.include_cli_output

        def sinks(name: str) -> tuple[EventSink, ...]:
            """Create agent-specific output using this build's captured scope."""
            return default_event_sinks(
                data_path=sandbox.project_logs_dir() / name, include_cli=include_cli
            )

        agent = replace(
            self.agent,
            capabilities=(
                *self.agent.capabilities,
                *self.additional_capabilities,
            ),
        )
        return agent.build_graph(event_sinks=caller_sinks, event_sink_factory=sinks)


__all__ = ["Deployment"]
