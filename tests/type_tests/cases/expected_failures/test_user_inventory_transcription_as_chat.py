from roboz import Agent
from tests.type_tests.fixtures.inventory_models import custom

Agent(name="invalid", system_prompt="Stop.", agent_endpoint=custom.audio)
