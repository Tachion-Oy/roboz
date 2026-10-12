"""Read PDF text and create PDFs through a single guarded tool."""

import math
import os
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.models import Empty, Message, Str
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.models import (
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    Operation,
    ParseError,
)
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.tools.cli_commands.commands.writers import write_operations
from roboz.shed.tools.cli_commands.paths import (
    resolve_single_file_path,
    resolve_tool_base,
)
from roboz.shed.tools.contexts import GuardContext
from roboz.shed.tools.guard import build_guarded_tool_chain, operation_guard
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool, factory
from roboz.tooling.context import HasExternalDependencies

EXPORT_FILTERS = {
    **dict.fromkeys((".html", ".htm", ".docx", ".odt"), "writer_pdf_Export"),
    **dict.fromkeys((".xlsx", ".ods"), "calc_pdf_Export"),
    **dict.fromkeys((".pptx", ".odp"), "impress_pdf_Export"),
}
PROFILE_SETTINGS = """<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">
<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
<prop oor:name="SecureURL" oor:op="fuse"><value/></prop>
</item></oor:items>"""


class PdfInput(Empty):
    """Choose reading an existing PDF or creating one from a source document."""

    operation: Literal["read", "create"]
    path: str = Field(description="Source file, relative to the tool base or absolute.")
    output_path: str | None = Field(
        default=None, description="Required for create; PDF destination."
    )
    start_page: int = Field(
        default=1, ge=1, description="Read from this page (one-based, inclusive)."
    )
    end_page: int | None = Field(
        default=None, ge=1, description="Read through this page; default: last."
    )
    overwrite: bool = Field(
        default=False, description="Allow create to replace an existing PDF."
    )


@dataclass(frozen=True)
class PdfContext(HasExternalDependencies):
    """File permissions, runtime events, and the optional PDF converter."""

    permissions: PermissionPolicy
    pipe: EventPipe
    libreoffice: ExecutableDependency = field(
        default_factory=lambda: ExecutableDependency("libreoffice")
    )
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        """Require a finite positive conversion timeout."""
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Declare LibreOffice without resolving it; reading does not require it."""
        return (self.libreoffice,)


def _validate_process(input: PdfInput, source: Path, base: Path) -> Path | None:
    if input.operation == "create":
        if input.start_page != 1 or input.end_page is not None:
            raise ValueError("Page ranges are only supported for read")
        if source.suffix.lower() not in EXPORT_FILTERS:
            raise ValueError("Create supports HTML, DOCX/ODT, XLSX/ODS, and PPTX/ODP")
        if input.output_path is None:
            raise ValueError("Create requires output_path")
        destination = resolve_single_file_path(input.output_path, base=base)
        if destination.suffix.lower() != ".pdf" or destination == source:
            raise ValueError("output_path must be a different file ending in .pdf")
        if destination.exists() and not input.overwrite:
            raise ValueError(
                "Destination already exists; use overwrite=true to replace it"
            )
        return destination
    if input.output_path is not None or input.overwrite:
        raise ValueError("output_path and overwrite are only supported for create")
    return None


@factory
def process_pdf(
    input: PdfInput,
    messages: list[Message],
    ctx: PdfContext,
) -> ResolvedFileCommand[PdfInput, PdfInput] | ParseError:
    """Read PDF text or create a PDF from HTML or Office documents.

    Reading ignores images and performs no OCR. Use inclusive page ranges to
    read long documents in parts. Creation disables external resource loading
    and macros; write HTML with the filesystem tools to author a new document.
    Existing destinations require overwrite=true and replacement permissions.
    """
    base = resolve_tool_base(ctx.permissions.base)
    try:
        source = resolve_single_file_path(input.path, base=base)
        if not source.is_file():
            raise ValueError(f"Source must be an existing regular file: {source}")
        operations = [(Operation.READ, source)]
        destination = _validate_process(input, source, base)
        if input.operation == "create":
            assert input.output_path is not None
            operations += write_operations(input.output_path, base)
    except (OSError, ValueError) as error:
        return ParseError(message=str(error))
    payload = input.model_copy(
        update={
            "path": str(source),
            "output_path": str(destination) if destination else None,
        }
    )
    return ResolvedFileCommand[PdfInput, PdfInput](
        original_input=input,
        items=[
            GuardFileSingle[PdfInput](operation=operation, location=path, value=payload)
            for operation, path in operations
        ],
    )


def _read(input: PdfInput, ctx: PdfContext) -> str:
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
            ctx.pipe.raise_if_cancelled()
            text = reader.pages[index].extract_text() or "[No extractable text]"
            page = f"\n--- Page {index + 1} ---\n{text}\n"
            if len(output) + len(page) > limit:
                return output + page[: limit - len(output)] + notice
            output += page
        return output


def _wait_for_conversion(
    process: subprocess.Popen[bytes], ctx: PdfContext,
) -> tuple[bytes, bytes]:
    deadline = time.monotonic() + ctx.timeout_s
    while True:
        ctx.pipe.raise_if_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"LibreOffice timed out after {ctx.timeout_s:g} seconds")
        try:
            return process.communicate(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            continue


def _stop_conversion(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    process.wait()


def _convert(argv: list[str], ctx: PdfContext) -> None:
    with subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            stdout, stderr = _wait_for_conversion(process, ctx)
        finally:
            _stop_conversion(process)
    if process.returncode:
        detail = (stderr or stdout).decode(errors="replace")[:2000].strip()
        raise ValueError(f"LibreOffice exited with {process.returncode}: {detail}")


def _create(input: PdfInput, ctx: PdfContext, *, replace_existing: bool) -> str:
    assert input.output_path is not None
    destination, source = Path(input.output_path), Path(input.path)
    executable = ctx.libreoffice.require()
    # Staging beside the destination keeps publication on the same filesystem.
    with TemporaryDirectory(prefix=".roboz-pdf-", dir=destination.parent) as directory:
        root = Path(directory)
        profile = root / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user/registrymodifications.xcu").write_text(
            PROFILE_SETTINGS, encoding="utf-8"
        )
        staged_source = root / ("source" + source.suffix.lower())
        shutil.copyfile(source, staged_source)
        _convert(
            [
                str(executable),
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--nologo",
                "--norestore",
                "--convert-to",
                f"pdf:{EXPORT_FILTERS[source.suffix.lower()]}",
                "--outdir",
                str(root),
                str(staged_source),
            ],
            ctx,
        )
        output = root / "source.pdf"
        if not output.is_file():
            raise ValueError(
                "LibreOffice produced no PDF; check the source document and installed filters"
            )
        with output.open("rb") as stream:
            pages = len(PdfReader(stream).pages)
        if not pages:
            raise ValueError("LibreOffice produced an empty PDF")
        ctx.pipe.raise_if_cancelled()
        if replace_existing:
            os.replace(output, destination)
        else:
            os.link(
                output, destination
            )  # Atomic publication; never clobber a new arrival.
    return f"Created {destination} ({pages} pages)"


@factory
def execute_pdf(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: PdfContext,
) -> Str:
    """Execute the permitted PDF operation and report its result."""
    if input.status != GuardStatus.ALLOWED:
        return Str(value=input.message or "PDF operation denied")
    request = input.items[0].value
    assert isinstance(request, PdfInput)
    try:
        if request.operation == "read":
            value = _read(request, ctx)
        else:
            # READ/DELETE are included only when replacement was actually guarded.
            value = _create(request, ctx, replace_existing=len(input.items) == 4)
        return Str(value=value)
    except (OSError, ValueError, PyPdfError) as error:
        return Str(value=f"PDF {request.operation} failed: {error}")


def get_pdf_tool(ctx: PdfContext) -> list[Tool]:
    """Build the single PDF entry tool and its passive guard/executor chain."""
    policy = ctx.permissions
    guard = GuardContext(
        base=resolve_tool_base(policy.base),
        takes_precedence=policy.takes_precedence,
        default_verdict=policy.default_verdict,
        allow=list(policy.allow),
        deny=list(policy.deny),
        ask=list(policy.ask),
        pipe=ctx.pipe,
    )
    return build_guarded_tool_chain(
        entry=process_pdf(ctx),
        guard=operation_guard(guard),
        execute=execute_pdf(ctx),
    )
