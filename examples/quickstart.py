import roboz as rz
from roboz.llm import MockLLMEndpoint


class LunchPreference(rz.Empty):
    cuisine: str | None


class Restaurant(rz.Empty):
    name: str
    seats_available: bool


class Booking(rz.Empty):
    confirmation: str


steps: list[str] = []
preferences = [
    LunchPreference(cuisine="sushi"),
    LunchPreference(cuisine="pizza"),
]


@rz.tool
def plan_lunch_with_bob(
    input: rz.Empty, messages: list[rz.Message]
) -> LunchPreference:
    """Ask Bob which supported cuisine he wants for lunch."""
    steps.append("plan_lunch_with_bob")
    return preferences.pop(0)


@rz.tool
def retry_plan_lunch_with_bob(
    input: Restaurant, messages: list[rz.Message]
) -> LunchPreference:
    """Ask Bob for another cuisine after a restaurant has no seats."""
    steps.append("retry_plan_lunch_with_bob")
    return preferences.pop(0)


@rz.tool(
    chained_to=[plan_lunch_with_bob, retry_plan_lunch_with_bob],
    chain_condition=lambda output: (
        isinstance(output, LunchPreference)
        and output.cuisine is not None
    ),
)
def find_restaurant(
    input: LunchPreference, messages: list[rz.Message]
) -> Restaurant:
    """Find a restaurant for Bob's chosen cuisine and report seat availability."""
    steps.append(f"find_restaurant({input.cuisine})")
    if input.cuisine == "sushi":
        return Restaurant(name="Sushi Schema", seats_available=False)
    return Restaurant(name="The Typed Table", seats_available=True)


retry_plan_lunch_with_bob.chain(
    chained_to=find_restaurant,
    chain_condition=lambda output: (
        isinstance(output, Restaurant) and not output.seats_available
    ),
)


@rz.tool(
    chained_to=find_restaurant,
    chain_condition=lambda output: (
        isinstance(output, Restaurant) and output.seats_available
    ),
)
def book_a_table(input: Restaurant, messages: list[rz.Message]) -> Booking:
    """Book a table at a restaurant that has available seats."""
    steps.append("book_a_table")
    return Booking(confirmation=f"Table booked at {input.name}")


endpoint = MockLLMEndpoint(
    [
        {
            "action": "plan_lunch_with_bob",
            "rationale": "Ask Bob what he wants for lunch.",
        },
        {
            "action": "stop",
            "rationale": "Lunch has been arranged.",
            "value": "Lunch is booked.",
        },
    ]
)
agent = rz.Agent(
    name="lunch_planner",
    interaction_mode=None,
    system_prompt=(
        "Plan lunch with Bob. If a restaurant has no seats, ask him to choose "
        "again. Stop after booking a table."
    ),
    tools=[
        plan_lunch_with_bob,
        retry_plan_lunch_with_bob,
        find_restaurant,
        book_a_table,
        rz.stop,
    ],
    agent_endpoint=endpoint,
)

result, _messages = agent.invoke()
assert steps == [
    "plan_lunch_with_bob",
    "find_restaurant(sushi)",
    "retry_plan_lunch_with_bob",
    "find_restaurant(pizza)",
    "book_a_table",
]
print(" -> ".join(steps))
print(result.value)
