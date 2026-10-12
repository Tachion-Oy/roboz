"""PDF instructions and the guarded reader."""

from roboz.skill import Skill
from roboz.shed.tools.pdf import PdfContext, get_pdf_tool

pdf_skill = Skill.factory(
    name="pdf",
    description="Read PDF text with page ranges and file permission checks.",
    instructions=(
        "Use process_pdf with path to extract PDF text. "
        "start_page/end_page are inclusive and one-based; use smaller ranges "
        "if output is truncated. Images are ignored; no OCR is performed. "
        "Encrypted PDFs require a decrypted copy."
    ),
    build_tools=get_pdf_tool,
)

__all__ = ["PdfContext", "pdf_skill"]
