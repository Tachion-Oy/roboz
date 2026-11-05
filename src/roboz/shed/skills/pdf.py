"""PDF instructions and the guarded text, image, and page reader."""

from pathlib import Path

from roboz.runtime import EventPipe
from roboz.skill import Skill
from roboz.shed.models import ActionVerdict, PermissionRule
from roboz.shed.tools.pdf import MAX_PDF_IMAGES, get_pdf_tool


def pdf_skill(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict = ActionVerdict.deny,
    pipe: EventPipe | None = None,
) -> Skill:
    """Build the PDF skill with the same permission options as get_pdf_tool."""
    return Skill(
        name="pdf",
        description=(
            "Read PDF text, embedded images, or rendered page images with page ranges "
            "and file permission checks."
        ),
        instructions=(
            "Use process_pdf with path to extract PDF text. "
            "start_page/end_page are inclusive and one-based; use smaller ranges "
            "if output is truncated. No OCR is performed. "
            "Text is always returned; the images option chooses what else is attached. "
            "images=none (default) attaches nothing; use it for ordinary prose. "
            "images=embedded attaches the pictures stored inside the PDF, such as "
            "photos, figures saved as pictures, and scanned pages, at their own "
            "resolution but without the surrounding page. It misses charts, diagrams, "
            "and tables drawn as vector graphics, because those are drawing "
            "instructions rather than stored pictures. "
            "images=pages renders each selected page as one image, exactly as it looks: "
            "text, layout, vector charts, diagrams, tables, forms, and pictures together. "
            "Use pages when the visual appearance matters more than the extracted text, "
            "or when the text is garbled, out of order, or missing; where the page image "
            "and the text disagree, trust the image. It costs one image per page, so "
            "keep page ranges narrow. Use embedded only to inspect a specific photo or "
            "figure at full fidelity. "
            f"Both image modes attach at most {MAX_PDF_IMAGES} images per request and "
            "need an image-capable model. "
            "Encrypted PDFs require a decrypted copy."
        ),
        tools=get_pdf_tool(
            base=base,
            default_verdict=default_verdict,
            deny_rules=deny_rules,
            allow_rules=allow_rules,
            ask_rules=ask_rules,
            takes_precedence=takes_precedence,
            pipe=pipe,
        ),
    )


__all__ = ["pdf_skill"]
