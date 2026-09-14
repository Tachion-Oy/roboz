from builtins import input as read_input

from roboz import Agent, tool
from roboz.models import Empty, Int, Message, Stop


@tool
def ask_number(input: Empty, messages: list[Message]) -> Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        try:
            reply = read_input("Enter an integer: ")
        except EOFError:  # Keep the example runnable in non-interactive checks.
            reply = "7"
        try:
            return Int(value=int(reply))
        except ValueError:
            print("Please enter a whole number.")


@tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 == 0,
)
def report_even(input: Int, messages: list[Message]) -> Stop:
    """Report that the supplied integer is even."""
    print(f"{input.value} is even.")
    return Stop(value="even")


@tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 != 0,
)
def report_odd(input: Int, messages: list[Message]) -> Stop:
    """Report that the supplied integer is odd."""
    print(f"{input.value} is odd.")
    return Stop(value="odd")


agent = Agent(
    name="demo",
    is_agentic=False,
    agent_endpoint=None,
    default_tools=[ask_number],
    tools=[report_even, report_odd],
)

agent.invoke()
