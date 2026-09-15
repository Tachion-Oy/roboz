from builtins import input as read_input

from roboz import Agent, factory, tool
from roboz.llm import EndpointLike, MockLLMEndpoint, get_completion
from roboz.models import Empty, Int, Message, Role, Stop


@tool
def ask_number(input: Empty, messages: list[Message]) -> Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        reply = read_input("Enter an integer: ")
        try:
            return Int(value=int(reply))
        except ValueError:
            print("Please enter an integer :)")


@tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 == 0,
)
def report_even(input: Int, messages: list[Message]) -> Int:
    """Pass an even integer to the model-backed reviewer."""
    return input


@factory(chained_to=report_even)
def escalate(input: Int, messages: list[Message], ctx: EndpointLike) -> Stop:
    """Ask the configured model whether the supplied even integer is acceptable."""
    prompt = (
        "The user was prompted for an integer and they chose "
        f"{input.value}. Is this too hot for our system to handle?! (y/n)"
    )
    messages = [Message(role=Role.SYSTEM, content=prompt)]
    verdict = get_completion(endpoint=ctx, messages=messages)
    return Stop(value=verdict)


@tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 != 0,
)
def report_odd(input: Int, messages: list[Message]) -> Stop:
    """Accept an odd integer without requesting a second opinion."""
    return Stop(value=f"{input.value} is a fine and civilized choice, well done.")


review_endpoint = MockLLMEndpoint(["An even number? Too hot! Better stop here."])
escalate_tool = escalate(review_endpoint)

agent = Agent(
    name="demo",
    is_agentic=False,
    agent_endpoint=None,
    default_tools=[ask_number],
    tools=[report_even, escalate_tool, report_odd],
)

result, _messages = agent.invoke()
print(result.value)
