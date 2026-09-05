"""Model-facing instructions and schemas for invoking tools."""

import logging
from collections.abc import Sequence
from typing import Final, Literal

from pydantic import ConfigDict, create_model

from roboz.llm.prompts import JSON_OUTPUT_SELF_CHECK, JSON_STRING_ESCAPING_RULES
from roboz.models import BaseNames, Int, Message, Str
from roboz.models._schema import schema_scrubber
from roboz.tooling.core import Tool
from roboz.tooling.decorators import tool

logger = logging.getLogger(__name__)

TOOL_INVOCATION_INSTRUCTIONS: Final[str] = f"""## Tool Calls Only

The only valid output from you is a tool call to one of the tools listed below. Do not try to invoke tools that are not listed there or send a raw text message to address the user. In cases where interaction with the user is required, a specific tool for interacting with the user will be provided to you.

This holds for *every* turn without exception, including your very first message in a conversation and any turn right after you have reviewed memory or context. Greetings, acknowledgements, status updates, "here's what I found" narration, and summaries are all delivered by calling the appropriate user-interaction tool (with the text in its `{BaseNames.VALUE_FIELD}` field) — never as raw prose. If your reply does not begin with `{{`, it is wrong."""

PARSABLE_JSON_INSTRUCTIONS: Final[str] = """## JSON Output

Your output must *always* be a single, valid JSON object i.e. your response must start with `{` and end with `}`. Your response will be directly parsed by Python's `json.loads()` function and converted to a pydantic instance corresponding to the specification of your chosen tool. Do NOT under any circumstances start your response with addressing the user, commenting on the task etc. or wrap your response in any markdown. The *only* exception is the use of possible `<think>` tags (or something equivalent) indicating your reasoning trace, which are to be left in."""

OUTPUT_STRUCTURE_INSTRUCTIONS: Final[str] = """## JSON Structure

Every JSON object you produce MUST contain *at least* the two fields: `action` and `rationale`.
- `action`: A string that exactly matches the name of the tool you want to use.
- `rationale`: A string explaining your thought process i.e. your internal monologue for why you are taking the chosen action.
-  there can only be one `action` and one `rationale` per you output, i.e. you are to perform only one action at a time.

Depending on the required input arguments of the tools you decide to invoke, there of course can be other fields in your output. The Pydantic model schemas that are required for each specific tool call will be provided to you below, if applicable. If your output contains only the above two fields it means you are calling a tool without input arguments.

In the conversation you may see calls to tools that are not listed below as available. These are invoked automatically and cannot be invoked by you, the agent."""

JSON_OUTPUT_SHAPE_EXAMPLES: Final[str] = """## JSON shape (illustrative)

Valid — your **entire** reply is **exactly one** JSON object (additional fields depend on the tool you chose):

`{"action": "<tool_name>", "rationale": "<why am I taking this action>"}`

Invalid — never output two top-level objects in one reply. Concatenation so that the sequence `}{` appears in your message (e.g. two objects back-to-back) is **forbidden** and will not parse.

`{"action":"example_first","rationale":"…"}{"action":"example_second","rationale":"…"}` ← never do this; emit one object, wait for the next turn for the next call."""

TOOL_JSON_STRING_ESCAPING_EXAMPLE: Final[
    str
] = r"""Example — a single valid tool-call object whose `value` contains quotes, line breaks, a Windows path, an apostrophe, and an emoji, all correctly escaped:

`{"action": "prompt_user", "rationale": "Sharing the draft so it's reviewed before sending.", "value": "Here's the draft:\n\n\"Hi Elton — happy to help.\"\n\nSaved to C:\\Users\\notes.md 🚀"}`"""

JSON_STRING_ESCAPING_INSTRUCTIONS: Final[str] = (
    f"{JSON_STRING_ESCAPING_RULES}\n\n{TOOL_JSON_STRING_ESCAPING_EXAMPLE}"
)

AGENT_TOOL_USE_INSTRUCTIONS: Final[str] = f"""# Instructions on Tool Usage

{TOOL_INVOCATION_INSTRUCTIONS}

{PARSABLE_JSON_INSTRUCTIONS}

{OUTPUT_STRUCTURE_INSTRUCTIONS}

{JSON_OUTPUT_SHAPE_EXAMPLES}

{JSON_STRING_ESCAPING_INSTRUCTIONS}

{JSON_OUTPUT_SELF_CHECK}"""


@tool
def example_tool(input: Int, messages: list[Message]) -> Str:
    """Call this example with an integer to inspect tool invocation formatting."""
    return Str(value=f"Example: {input.value}")


@tool
def example_skill_tool(input: Str, messages: list[Message]) -> Str:
    """Call this example after loading the skill that provides it."""
    return Str(value=f"Skill Tool Example: {input.value}")


def _get_tool_call_schema(tool: Tool) -> dict:
    ToolCallModel = create_model(
        tool.name,
        __base__=tool.InputModel,
        __config__=ConfigDict(extra="forbid"),
        action=(Literal[tool.name], ...),
        rationale=(str, ...),
    )
    return schema_scrubber(ToolCallModel.model_json_schema())


def _get_single_instruction(tool: Tool) -> str:
    tool_use_prompt = f"### `{tool.name}`\n"
    tool_use_prompt += (
        f"description: {tool.description}\n"
        if tool.description
        else "description: Description evident from name.\n"
    )
    tool_use_prompt += f"input Pydantic model: {_get_tool_call_schema(tool)}"
    return tool_use_prompt


def get_tool_instructions(
    tools: Sequence[Tool | None] | Sequence[Tool],
    with_agent_tool_use_instructions: bool = True,
) -> str:
    if not tools:
        return ""
    if with_agent_tool_use_instructions:
        tool_use_prompt: str = (
            f"""{AGENT_TOOL_USE_INSTRUCTIONS}\n\n## Available Tools\n\n"""
        )
    else:
        tool_use_prompt = """## Available Tools\n\n"""

    for t in tools:
        if t is None or t.chained_to:
            continue
        tool_use_prompt += f"{_get_single_instruction(t)}\n\n"
    return tool_use_prompt.rstrip()
