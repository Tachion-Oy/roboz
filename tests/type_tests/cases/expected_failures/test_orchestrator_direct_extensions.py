from roboshed.agents import orchestrator
from roboz.llm import MockLLMEndpoint

orchestrator(
    agent_endpoint=MockLLMEndpoint([]),
    tools=(),
    default_tools=(),
    skills=(),
    auto_loaded_skills=(),
)
