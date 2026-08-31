from typing import Sequence

from roboz.models import Empty, Invoke, Message, Stop, Str
from roboz.tooling.core import Tool
from roboz.tooling.decorators import tool


@tool
def stop(input: Str, messages: list[Message]) -> Stop:
    """Gracefully stop the process and output a value."""
    return Stop(value=input.value)


def stop_after(
    cleanup_tools: Tool | Sequence[Tool | Sequence[Tool]] | None = None,
    *,
    final_stop_tool: Tool = stop,
) -> list[Tool]:
    """Build a stop command that runs cleanup tools before final Stop."""
    if cleanup_tools is None:
        return [final_stop_tool]
    flattened = (
        [cleanup_tools]
        if isinstance(cleanup_tools, Tool)
        else Tool.to_tool_list(cleanup_tools)
    )

    original_stop_value = ""

    @tool
    def stop_after_entry(input: Str, messages: list[Message]) -> Str:
        nonlocal original_stop_value
        original_stop_value = input.value
        return Str(value=input.value)

    stop_entry = stop_after_entry.copy(name=stop.name, description=stop.description)
    chained_cleanup: list[Tool] = []
    previous: Tool = stop_entry
    for cleanup in flattened:
        copied_cleanup = cleanup.copy(chained_to=previous)
        chained_cleanup.append(copied_cleanup)
        previous = copied_cleanup

    @tool(chained_to=previous)
    def stop_after_finalize(
        input: Empty, messages: list[Message]
    ) -> Empty | Invoke | Stop:
        return final_stop_tool(Str(value=original_stop_value), messages)

    return [stop_entry, *chained_cleanup, stop_after_finalize]
