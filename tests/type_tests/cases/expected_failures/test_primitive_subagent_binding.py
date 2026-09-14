"""Subagent binding requires the concrete child agent."""

from roboz.agent import run_subagent

# Expected: reportArgumentType; run_subagent requires Agent.
run_subagent("child")
