"""Run a credential-free agent that returns a random Simpsons quote."""

from random import choice

from roboz import Agent, tool
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Message, Stop

from .simpsons_quotes import QUOTES


@tool
def get_quote(input: Empty, messages: list[Message]) -> Stop:
    """Return a random Simpsons quote and then stop."""
    return Stop(value=choice(QUOTES))


def main() -> None:
    """Build and run the credential-free example agent."""
    mock = MockLLMEndpoint(
        responses=[{"action": "get_quote", "rationale": "Need Simpsons quote!"}]
    )

    agent = Agent(
        name="demo",
        system_prompt="You are a Simpsons quote generator",
        agent_endpoint=mock,
        tools=[get_quote],
    )

    output, messages_ = agent.invoke()
    print(f'"{output.value}"')


if __name__ == "__main__":
    main()
