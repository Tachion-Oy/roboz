"""PDF instructions and the guarded text and image reader."""

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
            "Read PDF text and embedded images with page ranges and file permission checks."
        ),
        instructions=(
            "Use process_pdf with path to extract PDF text. "
            "start_page/end_page are inclusive and one-based; use smaller ranges "
            "if output is truncated. No OCR is performed. "
            "Set images to true to also attach the photos, figures, and scanned pages "
            f"embedded in the selected pages, up to {MAX_PDF_IMAGES} per request; "
            "this needs an image-capable model. Charts drawn as vector graphics are "
            "not images, so only their extractable text appears. "
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
