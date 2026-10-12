"""PDF instructions and the guarded read/create tool."""

from roboz.skill import Skill
from roboz.shed.tools.pdf import PdfContext, get_pdf_tool

pdf_skill = Skill.factory(
    name="pdf",
    description="Read PDF text and create PDFs from HTML or Office documents.",
    instructions=(
        "Use process_pdf with operation='read' and path to extract PDF text. "
        "start_page/end_page are inclusive and one-based; use smaller ranges "
        "if output is truncated. Images are ignored; no OCR is performed. "
        "To create a PDF, use operation='create', path to an HTML, DOCX/ODT, "
        "XLSX/ODS, or PPTX/ODP file, and output_path ending in .pdf. "
        "Author new reports as HTML with the filesystem tools first. "
        "The destination directory must exist. Replacing a PDF requires "
        "overwrite=true and replacement permissions. LibreOffice must be installed "
        "for creation. External resource loading and macros are disabled."
    ),
    build_tools=get_pdf_tool,
)

__all__ = ["PdfContext", "pdf_skill"]
