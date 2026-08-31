import roboz as rz
from roboz.llm import MockLLMEndpoint

mock_endpoint = MockLLMEndpoint(
    [{"action": "stop", "rationale": "Task Done!", "value": "Hello from Roboz!"}]
)
agent = rz.Agent(
    name="demo",
    system_prompt="Stop and return a greeting.",
    tools=[rz.stop],
    agent_endpoint=mock_endpoint,
)

result, _messages = agent.invoke()
print(result.value)
