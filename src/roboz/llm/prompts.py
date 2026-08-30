from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from roboz.models import Empty
from roboz.models._schema import extract_fields_and_type_names, schema_scrubber

JSON_FIX_PROMPT: Final[
    str
] = """Your previous output contained unparsable JSON. Importantly, everything you output must be parsable with python's json.loads() function. Specifically, two top-level JSON objects in one assistant message are *strictly prohibited* — for example when `}{` appears, or when a complete second JSON object follows the first (including on the next line after its closing brace).

The error trace:
"""
PYDANTIC_FIX_PROMPT: Final[
    str
] = """Your previous output did not adhere to the designated Pydantic models. Review them from the instructions and try again

The error trace:
"""
ACTION_FIX_PROMPT: Final[
    str
] = """Your previous output invoked a tool or a skill that does not exist or did not include a name in the `action` field. Review the available tools and skills from the provided instructions and try again

The error trace:
"""

STRUCTURED_JSON_OUTPUT_INSTRUCTIONS: Final[str] = """## JSON Output

Your output must *always* be a single, valid JSON object: the response must start with `{` and end with `}`. It will be parsed by Python's `json.loads()` function and validated against the Pydantic schema below. Do not add a preamble, commentary, Markdown fences, or any content outside the JSON object. The only exception is a provider-generated `<think>` block (or equivalent) before the JSON object."""

JSON_STRING_ESCAPING_RULES: Final[str] = r"""## Escaping text inside string values

Any text you place in a string field (such as `rationale` or a tool's `value`) must be escaped so the whole reply still parses with `json.loads()`. Get this right — it is the most common way a reply fails to parse:

- A double quote inside the text becomes `\"`.
- A backslash becomes `\\` (e.g. a Windows path `C:\Users` is written `C:\\Users`).
- A line break becomes `\n` — never a literal newline inside the string. Tabs become `\t`.
- Do **not** escape apostrophes or single quotes: write `it's`, not `it\'s`. `\'` is *invalid* JSON and will fail to parse.
- The only valid backslash escapes are `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, and `\uXXXX` (with four hex digits). Any other backslash sequence is invalid — emit the character directly (letters, punctuation, and emoji such as 🚀 need no escaping).
- Never let the reply end in the middle of a string. Finish and close every string and the outer object; a truncated reply cannot be parsed."""

JSON_OUTPUT_SELF_CHECK: Final[str] = r"""## Before you send

Silently verify: the whole reply is one JSON value starting with `{` and ending with `}`; nothing may follow the final `}`; every `"` and `\` inside a string value is escaped (`\"`, `\\`), line breaks are `\n`, and no apostrophe is backslash-escaped; `json.loads()` on the reply must succeed with **no** extra data. If any check fails, output only the corrected single object (no apology or explanation)."""


def get_basic_system_prompt(
    *,
    system_prompt: str = "",
    OutputModel: type[Empty],
    output_example: Mapping[str, object] | None = None,
) -> str:
    output_structure_instructions = (
        "## Output Pydantic model\n\nYour output "
        "must without exception follow the Pydantic schema"
    )
    scrubbed_schema = schema_scrubber(OutputModel.model_json_schema())
    parts = [
        system_prompt.strip(),
        STRUCTURED_JSON_OUTPUT_INSTRUCTIONS,
        JSON_STRING_ESCAPING_RULES,
        output_structure_instructions,
        f"```json\n{json.dumps(scrubbed_schema, indent=2, ensure_ascii=False)}\n```",
    ]
    if output_example is not None:
        validated_example = OutputModel.model_validate(dict(output_example))
        serialized_example = validated_example.model_dump(
            mode="json", exclude=set(extract_fields_and_type_names()[0])
        )
        parts.extend(
            [
                "## Valid output example",
                f"```json\n{json.dumps(serialized_example, ensure_ascii=False)}\n```",
            ]
        )
    parts.append(JSON_OUTPUT_SELF_CHECK)
    return "\n\n".join(part for part in parts if part)
