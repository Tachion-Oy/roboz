from typing import assert_type

from roboshed.deployments.robosprawl import AgenticFactory, RoboSprawlBundle
from roboz import Agent


def inspect(factory: AgenticFactory) -> None:
    bundle = factory.build()
    assert_type(bundle, RoboSprawlBundle)
    assert_type(bundle.agent, Agent)
    assert_type(bundle.background_agents, tuple[Agent, ...])
    agent, background_agents = bundle
    assert_type(agent, Agent)
    assert_type(background_agents, tuple[Agent, ...])
