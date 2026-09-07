from builtins import input as read_input

import roboz as rz


@rz.tool
def ask_number(input: rz.Empty, messages: list[rz.Message]) -> rz.Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        try:
            reply = read_input("Enter an integer: ")
        except EOFError:  # Keep the example runnable in non-interactive checks.
            reply = "7"
        try:
            return rz.Int(value=int(reply))
        except ValueError:
            print("Please enter a whole number.")


@rz.tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 == 0,
)
def report_even(input: rz.Int, messages: list[rz.Message]) -> rz.Stop:
    """Report that the supplied integer is even."""
    print(f"{input.value} is even.")
    return rz.Stop(value="even")


@rz.tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 != 0,
)
def report_odd(input: rz.Int, messages: list[rz.Message]) -> rz.Stop:
    """Report that the supplied integer is odd."""
    print(f"{input.value} is odd.")
    return rz.Stop(value="odd")


agent = rz.Agent(
    name="demo",
    is_agentic=False,
    agent_endpoint=None,
    default_tools=[ask_number],
    tools=[report_even, report_odd],
)

agent.invoke()
