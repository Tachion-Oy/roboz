import roboz as rz
from roboz.llm import MockLLMEndpoint

agent = rz.Agent(
    name="demo",
    system_prompt="Stop and return a greeting.",
    tools=[rz.stop],
    agent_endpoint=MockLLMEndpoint(
        [
            {
                "action": "stop",
                "rationale": "The task is complete.",
                "value": "Hello from Roboz!",
            }
        ]
    ),
)

result, _messages = agent.invoke()
print(result.value)
