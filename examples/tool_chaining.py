import roboz as rz
from roboz.llm import MockLLMEndpoint


@rz.tool
def classify_value(input: rz.Str, messages: list[rz.Message]) -> rz.Int | rz.Str:
    """Classify the supplied value as an integer or text."""
    try:
        return rz.Int(value=int(input.value))
    except ValueError:
        return input


@rz.tool(
    chained_to=classify_value,
    chain_condition=lambda output: isinstance(output, rz.Int),
)
def describe_number(input: rz.Int, messages: list[rz.Message]) -> rz.Str:
    """Describe a classified integer."""
    return rz.Str(value=f"{input.value} is a number")


@rz.tool(
    chained_to=classify_value,
    chain_condition=lambda output: isinstance(output, rz.Str),
)
def describe_text(input: rz.Str, messages: list[rz.Message]) -> rz.Str:
    """Describe classified text."""
    return rz.Str(value=f"{input.value!r} is text")


@rz.tool(chained_to=[describe_number, describe_text])
def finish_description(input: rz.Str, messages: list[rz.Message]) -> rz.Stop:
    """Return the classification description as the final result."""
    return rz.Stop(value=input.value)


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
    agent = rz.Agent(
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
