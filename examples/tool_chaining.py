from roboz import Agent, tool
from roboz.llm import MockLLMEndpoint
from roboz.models import Int, Message, Stop, Str


@tool
def classify_value(input: Str, messages: list[Message]) -> Int | Str:
    """Classify the supplied value as an integer or text."""
    try:
        return Int(value=int(input.value))
    except ValueError:
        return input


@tool(
    chained_to=classify_value,
    chain_condition=lambda output: isinstance(output, Int),
)
def describe_number(input: Int, messages: list[Message]) -> Str:
    """Describe a classified integer."""
    return Str(value=f"{input.value} is a number")


@tool(
    chained_to=classify_value,
    chain_condition=lambda output: isinstance(output, Str),
)
def describe_text(input: Str, messages: list[Message]) -> Str:
    """Describe classified text."""
    return Str(value=f"{input.value!r} is text")


@tool(chained_to=[describe_number, describe_text])
def finish_description(input: Str, messages: list[Message]) -> Stop:
    """Return the classification description as the final result."""
    return Stop(value=input.value)


def classify(value: str) -> str | None:
    """Run one model-selected entry point followed by its passive tool chain."""
    endpoint = MockLLMEndpoint(
        [
            {
                "action": "classify_value",
                "rationale": "The value needs classification.",
                "value": value,
            }
        ]
    )
    agent = Agent(
        name="classifier",
        system_prompt="Classify the supplied value.",
        tools=[
            classify_value,
            describe_number,
            describe_text,
            finish_description,
        ],
        agent_endpoint=endpoint,
    )
    result, _messages = agent.invoke()
    return result.value


print(classify("42"))
print(classify("hello"))
