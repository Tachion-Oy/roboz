"""PDF instructions and the guarded reader."""

from typing import Unpack

from roboz.skill import Skill
from roboz.shed.sandbox import ToolOptions
from roboz.shed.tools.pdf import get_pdf_tool


def pdf_skill(**options: Unpack[ToolOptions]) -> Skill:
    """Build the PDF skill from PermissionPolicy.tool_options(pipe)."""
    return Skill(
        name="pdf",
        description="Read PDF text with page ranges and file permission checks.",
        instructions=(
            "Use process_pdf with path to extract PDF text. "
            "start_page/end_page are inclusive and one-based; use smaller ranges "
            "if output is truncated. Images are ignored; no OCR is performed. "
            "Encrypted PDFs require a decrypted copy."
        ),
        tools=get_pdf_tool(**options),
    )


__all__ = ["pdf_skill"]
