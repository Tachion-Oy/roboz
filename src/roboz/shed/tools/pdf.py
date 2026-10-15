"""Read PDF text through a guarded tool."""

from pathlib import Path

from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from roboz.models import Empty, Message, Str
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.models import (
    ActionVerdict,
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    Operation,
    ParseError,
    PermissionRule,
)
from roboz.shed.tools.cli_commands.paths import (
    resolve_single_file_path,
    resolve_tool_base,
)
from roboz.shed.tools.contexts import GuardContext
from roboz.shed.tools.guard import build_guarded_tool_chain, operation_guard
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool, factory


class PdfInput(Empty):
    """Select an existing PDF and an optional page range to read."""

    path: str = Field(description="Source file, relative to the tool base or absolute.")
    start_page: int = Field(
        default=1, ge=1, description="Read from this page (one-based, inclusive)."
    )
    end_page: int | None = Field(
        default=None, ge=1, description="Read through this page; default: last."
    )


@factory
def process_pdf(
    input: PdfInput,
    messages: list[Message],
    ctx: Path,
) -> ResolvedFileCommand[PdfInput, PdfInput] | ParseError:
    """Read PDF text, ignoring images and performing no OCR.

    Use inclusive page ranges to read long documents in parts.
    """
    try:
        source = resolve_single_file_path(input.path, base=ctx)
        if not source.is_file():
            raise ValueError(f"Source must be an existing regular file: {source}")
    except (OSError, ValueError) as error:
        return ParseError(message=str(error))
    payload = input.model_copy(update={"path": str(source)})
    return ResolvedFileCommand[PdfInput, PdfInput](
        original_input=input,
        items=[
            GuardFileSingle[PdfInput](
                operation=Operation.READ, location=source, value=payload
            )
        ],
    )


def _read(input: PdfInput, pipe: EventPipe | None) -> str:
    with open(input.path, "rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are unsupported; provide a decrypted copy")
        total = len(reader.pages)
        end = input.end_page if input.end_page is not None else total
        if not input.start_page <= end <= total:
            raise ValueError(f"Page range must fall within 1–{total}")
        output = f"PDF: {total} pages. Images are not interpreted.\n"
        notice = "\n[Text truncated; request a smaller page range.]"
        limit = LIGHT_MAX_CHARS - len(notice)
        for index in range(input.start_page - 1, end):
            if pipe is not None:
                pipe.raise_if_cancelled()
            text = reader.pages[index].extract_text() or "[No extractable text]"
            page = f"\n--- Page {index + 1} ---\n{text}\n"
            if len(output) + len(page) > limit:
                return output + page[: limit - len(output)] + notice
            output += page
        return output


@factory
def execute_pdf(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: EventPipe | None,
) -> Str:
    """Read the permitted PDF and report its text."""
    if input.status != GuardStatus.ALLOWED:
        return Str(value=input.message or "PDF read denied")
    request = input.items[0].value
    assert isinstance(request, PdfInput)
    try:
        return Str(value=_read(request, ctx))
    except (OSError, ValueError, PyPdfError) as error:
        return Str(value=f"PDF read failed: {error}")


def get_pdf_tool(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict = ActionVerdict.deny,
    pipe: EventPipe | None = None,
) -> list[Tool]:
    """Build the PDF reader and its passive guard/executor chain.

    Supply the same absolute base and permission options as the other file
    tools, directly or through PermissionPolicy.tool_options(pipe).
    """
    resolved_base = resolve_tool_base(base)
    guard = GuardContext(
        base=resolved_base,
        takes_precedence=takes_precedence,
        default_verdict=default_verdict,
        allow=list(allow_rules or []),
        deny=list(deny_rules or []),
        ask=list(ask_rules or []),
        pipe=pipe,
    )
    return build_guarded_tool_chain(
        entry=process_pdf(resolved_base),
        guard=operation_guard(guard),
        execute=execute_pdf(pipe),
    )
