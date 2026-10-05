"""Nested agent binding requires the concrete child agent."""

from roboz.agent import run_nested_agent

# Expected: reportArgumentType; run_nested_agent requires Agent.
run_nested_agent("child")
