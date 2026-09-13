"""Background context constructor fields are statically checked."""

from roboz.agent import BackgroundAgentContext

# Expected: reportArgumentType; the agent field requires Agent.
BackgroundAgentContext(agent="child")
