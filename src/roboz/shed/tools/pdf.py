"""Read PDF text and optionally its embedded images through a guarded tool."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL.Image import Image as Picture
from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from roboz.models import Empty, Image, Message, Str, StrImage
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
from roboz.shed.tools.images import (
    JPEG_MEDIA_TYPE,
    JPEG_SIGNATURE,
    PNG_MEDIA_TYPE,
    ImageDetail,
    unsupported_image_model,
)
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool, factory

MAX_PDF_IMAGES = 20
IMAGES_NOT_INTERPRETED = "Images are not interpreted."
IMAGE_CAP_NOTICE = f"[Stopped at {MAX_PDF_IMAGES} images; request a smaller page range.]"
# Bullets, rules, and icons below this size on both sides are not worth attaching.
_MIN_IMAGE_SIDE = 64
_JPEG_MODES = {"L", "RGB"}
_PNG_MODES = {"1", "L", "LA", "P", "RGB", "RGBA"}
_SUFFIXES = {JPEG_MEDIA_TYPE: ".jpg", PNG_MEDIA_TYPE: ".png"}
_DECODE_ERRORS = (OSError, ValueError, KeyError, NotImplementedError, PyPdfError)


class PdfInput(Empty):
    """Select an existing PDF and an optional page range to read."""

    path: str = Field(description="Source file, relative to the tool base or absolute.")
    start_page: int = Field(
        default=1, ge=1, description="Read from this page (one-based, inclusive)."
    )
    end_page: int | None = Field(
        default=None, ge=1, description="Read through this page; default: last."
    )
    images: bool = Field(
        default=False,
        description="Also attach images embedded in the selected pages for visual inspection.",
    )
    detail: ImageDetail = Field(
        default=None, description="Detail level for attached images; omit for the model default."
    )


@factory
def process_pdf(
    input: PdfInput,
    messages: list[Message],
    ctx: Path,
) -> ResolvedFileCommand[PdfInput, PdfInput] | ParseError:
    """Read PDF text, optionally attaching its embedded images; performs no OCR.

    Use inclusive page ranges to read long documents in parts. Charts drawn as
    vector graphics are not images and are not attached.
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


def _read(
    input: PdfInput, pipe: EventPipe | None, *, with_images: bool
) -> tuple[str, list[Image]]:
    with open(input.path, "rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are unsupported; provide a decrypted copy")
        total = len(reader.pages)
        end = input.end_page if input.end_page is not None else total
        if not input.start_page <= end <= total:
            raise ValueError(f"Page range must fall within 1–{total}")
        pages = range(input.start_page - 1, end)
        images: list[Image] = []
        report = IMAGES_NOT_INTERPRETED
        if with_images:
            collector = _EmbeddedImages(reader, input.detail, pipe)
            report = collector.collect(pages)
            images = collector.images
        output = f"PDF: {total} pages. {report}\n"
        notice = "\n[Text truncated; request a smaller page range.]"
        limit = LIGHT_MAX_CHARS - len(notice)
        for index in pages:
            if pipe is not None:
                pipe.raise_if_cancelled()
            text = reader.pages[index].extract_text() or "[No extractable text]"
            page = f"\n--- Page {index + 1} ---\n{text}\n"
            if len(output) + len(page) > limit:
                return output + page[: limit - len(output)] + notice, images
            output += page
        return output, images


class _EmbeddedImages:
    """Collect distinct embedded images from a page range, up to the cap."""

    def __init__(
        self, reader: PdfReader, detail: ImageDetail, pipe: EventPipe | None
    ) -> None:
        self._reader = reader
        self._detail: ImageDetail = detail
        self._pipe = pipe
        self._seen: set[bytes] = set()
        self._lines: list[str] = []
        self.images: list[Image] = []

    def collect(self, pages: range) -> str:
        """Attach images in page order and report every attached or failed one."""
        slots = (
            (page, position)
            for page in pages
            for position in range(len(self._reader.pages[page].images))
        )
        for page, position in slots:
            if self._pipe is not None:
                self._pipe.raise_if_cancelled()
            try:
                encoded = self._extract(page, position)
            except _DECODE_ERRORS as error:
                self._lines.append(f"- {self._label(page, position)}: could not be read ({error})")
                continue
            if encoded is None or not self._is_new(encoded[0]):
                continue
            if len(self.images) == MAX_PDF_IMAGES:
                self._lines.append(IMAGE_CAP_NOTICE)
                break
            self._attach(encoded, page, position)
        return "\n".join((f"Attached {len(self.images)} embedded images.", *self._lines))

    def _extract(self, page: int, position: int) -> tuple[bytes, str] | None:
        """Decode one embedded image; None for icons too small to be worth attaching."""
        embedded = self._reader.pages[page].images[position]
        if self._is_icon(embedded.image):
            return None
        return self._encode(embedded.data, embedded.image)

    def _is_new(self, data: bytes) -> bool:
        """Record the image bytes, reporting whether they had not been seen before."""
        digest = sha256(data).digest()
        if digest in self._seen:
            return False
        self._seen.add(digest)
        return True

    def _attach(self, encoded: tuple[bytes, str], page: int, position: int) -> None:
        data, media_type = encoded
        name = f"page{page + 1}-image{position + 1}{_SUFFIXES[media_type]}"
        self.images.append(
            Image.from_bytes(data, media_type=media_type, name=name, detail=self._detail)
        )
        self._lines.append(f"- Image {len(self.images)}: {self._label(page, position)}")

    @staticmethod
    def _label(page: int, position: int) -> str:
        return f"page {page + 1}, image {position + 1}"

    @staticmethod
    def _is_icon(picture: Picture | None) -> bool:
        return picture is not None and max(picture.size) < _MIN_IMAGE_SIDE

    @staticmethod
    def _encode(data: bytes, picture: Picture | None) -> tuple[bytes, str]:
        """Keep plain JPEG bytes; re-encode every other format as PNG."""
        if picture is None:
            raise ValueError("unsupported image encoding")
        if data.startswith(JPEG_SIGNATURE) and picture.mode in _JPEG_MODES:
            return data, JPEG_MEDIA_TYPE
        if picture.mode not in _PNG_MODES:
            picture = picture.convert("RGB")
        buffer = BytesIO()
        picture.save(buffer, format="PNG")
        return buffer.getvalue(), PNG_MEDIA_TYPE


@factory
def execute_pdf(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: EventPipe | None,
) -> Str | StrImage:
    """Read the permitted PDF and report its text and any requested images."""
    if input.status != GuardStatus.ALLOWED:
        return Str(value=input.message or "PDF read denied")
    request = input.items[0].value
    assert isinstance(request, PdfInput)
    unsupported = unsupported_image_model() if request.images else None
    try:
        text, images = _read(request, ctx, with_images=request.images and unsupported is None)
    except (OSError, ValueError, PyPdfError) as error:
        return Str(value=f"PDF read failed: {error}")
    if unsupported is not None:
        text = f"{unsupported.value}\n{text}"
    return StrImage(value=text, images=images) if request.images and images else Str(value=text)


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
