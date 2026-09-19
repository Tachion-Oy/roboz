from builtins import input as read_input

from roboz import Agent, factory, tool
from roboz.llm import EndpointLike, MockLLMEndpoint, get_completion
from roboz.models import Empty, Int, Message, Role, Stop, Str, filter_messages
from roboz.models.truncation import Severity, Truncation
from roboz.runtime.io import interact_with_user
from roboz.runtime.sinks import CliSink
from roboz.tools import stop


@tool
def ask_number(input: Empty, messages: list[Message]) -> Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        reply = read_input("Enter an integer: ")
        try:
            return Int(value=int(reply))
        except ValueError:
            print("Please enter an integer :)")


@factory(chained_to=ask_number, chain_condition=lambda x: x.value % 2 == 0)
def escalate(input: Int, messages: list[Message], ctx: EndpointLike) -> Stop | Str:
    """An even number?! Need to check this with HR!"""
    interact_with_user("Careful now, that is pretty spicy!", with_reply=False)
    prompt = f"The user chose {input.value}. Is this too hot to handle?! (y/n)?"
    verdict = get_completion(
        endpoint=ctx, messages=[Message(role=Role.SYSTEM, content=prompt)]
    )
    is_first_escalation = (
        len(filter_messages(caller="escalate", messages=messages)) == 0
    )
    if verdict == "y" and not is_first_escalation:
        return Stop(value="Too much spiciness, need to quit!")
    return Str(
        value="HR gave a pass, but still, let's show this to the agent only once.",
        truncation=Truncation(threshold=1, severity=Severity.REMOVE),
    )


@tool(chained_to=ask_number, chain_condition=lambda x: x.value % 2 != 0)
def give_praise(input: Int, messages: list[Message]) -> Str:
    """We need to give praise for such an erudite approach to the problem."""
    interact_with_user(f"{input.value} a fine and bold choice!", with_reply=False)
    return Str(value=f"{input.value} is good, no biggie.")


agent_endpoint = MockLLMEndpoint(
    responses=[
        *(10 * [{"action": "ask_number", "rationale": "This is my only job"}]),
        {"action": "stop", "rationale": "Enough numbers!", "value": ""},
    ]
)
guard_endpoint = MockLLMEndpoint(responses=10 * ["y"])

agent = Agent(
    name="demo",
    system_prompt=f"Without exception, use the {ask_number.name} tool.",
    event_sinks=[CliSink.default()],
    agent_endpoint=agent_endpoint,
    tools=[ask_number, escalate(guard_endpoint), give_praise, stop],
)
agent.invoke()
