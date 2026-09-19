from typing import Literal, assert_type

from roboz.agent import AgentMode as LegacyAgentMode
from roboz.deployment import DeployableAgent
from roboz.models import AgentMode


modes = tuple(AgentMode)
legacy_mode: LegacyAgentMode = AgentMode.STEERABLE
for mode in modes:
    configured: AgentMode = mode
    definition = DeployableAgent(name=mode, mode=configured)
    assert_type(definition.mode, AgentMode)
    assert_type(
        mode.value,
        Literal["deterministic", "steerable", "autonomous"],
    )
