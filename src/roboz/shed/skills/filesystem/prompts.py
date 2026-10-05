"""Combined instructions for guarded commands and literal file patches."""

from typing import Final

from roboz.shed.identifiers import APPLY_PATCH_TOOL_NAME, RUN_FILE_COMMAND_TOOL_NAME

from .cli import INSTRUCTIONS as CLI_INSTRUCTIONS
from .patch import INSTRUCTIONS as PATCH_INSTRUCTIONS

DESCRIPTION: Final[str] = (
    f"Use `{RUN_FILE_COMMAND_TOOL_NAME}` for guarded file discovery, searching, "
    "reading, writing, transfers, and deletion, and "
    f"`{APPLY_PATCH_TOOL_NAME}` for precise literal replacements. "
    "Covers file permissions, command syntax and chaining, and editing workflows."
)

INSTRUCTIONS: Final[str] = f"{CLI_INSTRUCTIONS}\n\n{PATCH_INSTRUCTIONS}"
