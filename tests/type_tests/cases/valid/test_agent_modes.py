from typing import Literal, assert_type

from roboz.agent import AgentMode
from roboz.deployment import DeployableAgent


modes = tuple(AgentMode)
for mode in modes:
    configured: AgentMode = mode
    definition = DeployableAgent(name=mode, mode=configured)
    assert_type(definition.mode, AgentMode)
    assert_type(
        mode.value,
        Literal["deterministic", "steerable", "autonomous"],
    )
