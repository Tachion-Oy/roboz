"""Combined instructions for guarded commands, patches, and image reading."""

from typing import Final

from roboz.shed.identifiers import (
    APPLY_PATCH_TOOL_NAME,
    READ_IMAGES_TOOL_NAME,
    RUN_FILE_COMMAND_TOOL_NAME,
)

from .cli import INSTRUCTIONS as CLI_INSTRUCTIONS
from .patch import INSTRUCTIONS as PATCH_INSTRUCTIONS

DESCRIPTION: Final[str] = (
    f"Use `{RUN_FILE_COMMAND_TOOL_NAME}` for guarded file discovery, searching, "
    "reading, writing, transfers, and deletion; "
    f"`{APPLY_PATCH_TOOL_NAME}` for precise literal replacements; and "
    f"`{READ_IMAGES_TOOL_NAME}` for visual inspection of local images. "
    "Covers file permissions, command syntax and chaining, editing, and image reading."
)

IMAGE_INSTRUCTIONS: Final[str] = f"""## Image reading

Use `{READ_IMAGES_TOOL_NAME}` to inspect PNG, JPEG, WebP, and GIF files visually.
Request several images together when comparing them or exploring a set:

```json
{{"action": "{READ_IMAGES_TOOL_NAME}", "rationale": "Compare the charts", "paths": ["before.png", "after.png"], "detail": "high"}}
```

Paths must name individual local files, relative to the configured working
directory or absolute. Globs and symlinks are unsupported. Each file must pass
its read permissions and any required approval. Failed or denied files are
reported, and the batch continues. The final report maps successful paths to
numbered image attachments in request order.

The optional `detail` setting applies to the whole batch: `auto`, `low`, or
`high`. Omit it for the model default. Images retain their original bytes;
provider format and size limits still apply.
"""

INSTRUCTIONS: Final[str] = (
    f"{CLI_INSTRUCTIONS}\n\n{PATCH_INSTRUCTIONS}\n\n{IMAGE_INSTRUCTIONS}"
)
