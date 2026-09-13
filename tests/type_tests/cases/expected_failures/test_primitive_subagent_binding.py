"""Subagent binding requires the concrete child agent."""

from roboz import run_subagent

# Expected: reportArgumentType; run_subagent requires Agent.
run_subagent("child")
