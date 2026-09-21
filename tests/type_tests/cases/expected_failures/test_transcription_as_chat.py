from roboz import Agent
from roboz.endpoints.inventory import groq

Agent(name="invalid", system_prompt="Stop.", agent_endpoint=groq.whisper_large_v3_turbo)
