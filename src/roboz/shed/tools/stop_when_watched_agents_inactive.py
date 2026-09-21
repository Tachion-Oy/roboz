"""Require a final maintenance sweep after watched agents become inactive."""

import json

from roboz.shed.identifiers import STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME
from roboz.shed.tools.contexts import StopWhenWatchedAgentsInactiveContext
from roboz.models import NO_MESSAGE, All, Message, Stop, Str, filter_messages
from roboz.runtime.persistence import active_marker_paths
from roboz.tooling.decorators import factory

_FINAL_SWEEP_REQUIRED = (
    f"{STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME}: final sweep required"
)


@factory
def stop_when_watched_agents_inactive(
    input: All, messages: list[Message], ctx: StopWhenWatchedAgentsInactiveContext
) -> Str | Stop:
    """Stop only after watched agents remain inactive for a full final sweep."""
    del input
    if active_marker_paths(ctx.conversation_root, ctx.agent_names):
        return Str(
            value=f"{STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME}: watched agents active",
            truncation=NO_MESSAGE,
        )

    previous_checks = filter_messages(
        messages, caller=STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME
    )
    last_result = (
        json.loads(previous_checks[-1].content).get("value")
        if previous_checks
        else None
    )
    if last_result == _FINAL_SWEEP_REQUIRED:
        # A complete maintenance sweep has run since the previous inactive check.
        return Stop(
            value=f"{STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME}: watched agents inactive"
        )

    # The preceding sweep may have missed a run's final output. Now that the
    # watched agents are inactive, require another sweep before allowing shutdown.
    return Str(
        value=_FINAL_SWEEP_REQUIRED,
        truncation=NO_MESSAGE,
    )


__all__ = ["StopWhenWatchedAgentsInactiveContext", "stop_when_watched_agents_inactive"]
