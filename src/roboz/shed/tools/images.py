"""Read batches of local images through filesystem permission guards."""

from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, PrivateAttr

from roboz.agent._execution_context import get_active_agent_endpoint
from roboz.llm import resolve_endpoint
from roboz.models import Empty, Image, Location, Message, Str, StrImage
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime import EventPipe
from roboz.shed.identifiers import READ_IMAGES_TOOL_NAME
from roboz.shed.models import (
    ActionVerdict,
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    Operation,
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


class ReadImagesInput(Empty):
    """Select local images and an optional detail level shared by the batch."""

    paths: list[str] = Field(
        min_length=1,
        description="Image files in order, relative to the tool base or absolute.",
    )
    detail: Literal["auto", "low", "high"] | None = Field(
        default=None, description="Detail level for every image; omit for the model default."
    )
    model_config = ConfigDict(extra="forbid")
    # The guard carries the concrete request through every passive step. Private
    # results survive its detached copies without serializing image data.
    _results: list[StrImage] = PrivateAttr(default_factory=list)
    _failure: str | None = PrivateAttr(default=None)


def _unsupported_model() -> Str | None:
    endpoint = get_active_agent_endpoint()
    if endpoint is None:
        return None
    selected = resolve_endpoint(endpoint)
    if selected.accepts_images:
        return None
    return Str(value=(
        f"The selected model, {selected.model_name}, cannot read images. "
        "Choose an image-capable model to inspect them. "
        "You can continue chatting with text."
    ))


def _prepare(
    request: ReadImagesInput, ctx: GuardContext
) -> ResolvedFileCommand[ReadImagesInput, Location] | Str:
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    unsupported = _unsupported_model()
    if unsupported is not None:
        return unsupported
    assert ctx.base is not None
    request._failure = None
    items: list[GuardFileSingle[Location]] = []
    try:
        source = resolve_single_file_path(
            request.paths[len(request._results)], base=ctx.base
        )
        if not source.is_file():
            raise ValueError(f"Source must be an existing regular file: {source}")
        items.append(
            GuardFileSingle[Location](
                operation=Operation.READ, location=source, value=Location(value=source)
            )
        )
    except (OSError, ValueError) as error:
        request._failure = str(error)
    return ResolvedFileCommand[ReadImagesInput, Location](
        original_input=request, items=items, truncation=NO_MESSAGE
    )


@factory
def read_images(
    input: ReadImagesInput, messages: list[Message], ctx: GuardContext
) -> ResolvedFileCommand[ReadImagesInput, Location] | Str:
    """Load local images for visual inspection in one batch.

    Supports PNG, JPEG, WebP, and GIF. Each file needs read permission. Failed
    files are reported while later files continue; successful attachments retain
    request order. Use literal file paths, without globs or symlinks.
    If the selected model cannot read images, choose an image-capable model
    or continue with text; repeating the same request will not enable vision.
    """
    return _prepare(input, ctx)


def _media_type(data: bytes) -> str:
    """Recognize supported signatures without decoding or changing pixels."""
    match data[:12]:
        case header if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        case header if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        case header if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        case header if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "image/webp"
        case _:
            raise ValueError("Unsupported image format; expected PNG, JPEG, WebP, or GIF")


@factory
def execute_read_image(
    input: GuardFilesResult, messages: list[Message], ctx: GuardContext
) -> GuardFilesResult | StrImage | Str:
    """Read one permitted image and retain its outcome before continuing."""
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    unsupported = _unsupported_model()
    if unsupported is not None:
        return unsupported
    request = input.original_input
    assert isinstance(request, ReadImagesInput)
    path = request.paths[len(request._results)]
    images: list[Image] = []
    if input.status != GuardStatus.ALLOWED:
        outcome = input.message or "Image read denied"
    elif request._failure is not None:
        outcome = f"Image read failed: {request._failure}"
    else:
        source = input.items[0].value
        assert isinstance(source, Location)
        try:
            data = source.value.read_bytes()
            if ctx.pipe is not None:
                ctx.pipe.raise_if_cancelled()
            unsupported = _unsupported_model()
            if unsupported is not None:
                return unsupported
            attachment = Image.from_bytes(
                data, media_type=_media_type(data), name=source.value.name,
                detail=request.detail,
            )
        except (OSError, ValueError) as error:
            outcome = f"Image read failed: {error}"
        else:
            images = [attachment]
            image_number = 1 + sum(len(result.images) for result in request._results)
            outcome = f"Loaded as image {image_number}"
    if input.status == GuardStatus.ALLOWED and input.message:
        outcome += f"\n{input.message}"
    request._results.append(
        StrImage(value=f"{len(request._results) + 1}. {path!r}: {outcome}", images=images)
    )
    if len(request._results) < len(request.paths):
        input.truncation = NO_MESSAGE
        return input
    attachments = [image for result in request._results for image in result.images]
    unsupported = _unsupported_model()
    if unsupported is not None:
        return unsupported
    return StrImage(
        value=f"Read {len(attachments)} of {len(request.paths)} images.\n"
        + "\n".join(result.value for result in request._results),
        images=attachments,
    )


@factory
def continue_read_images(
    input: GuardFilesResult, messages: list[Message], ctx: GuardContext
) -> ResolvedFileCommand[ReadImagesInput, Location] | Str:
    """Resolve the next image for an independent read permission check."""
    request = input.original_input
    assert isinstance(request, ReadImagesInput)
    return _prepare(request, ctx)


def get_read_images_tool(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict = ActionVerdict.deny,
    pipe: EventPipe | None = None,
) -> list[Tool]:
    """Build the batch image reader and its passive guard/execution loop.

    Supply the same absolute base and permission options as the other file
    tools, directly or through PermissionPolicy.tool_options(pipe).
    Only read_images is model-facing. Each path receives the supplied policy's
    read checks and approvals before its contents are opened. The final result
    reports every path and attaches successful images once, in request order.
    File signatures select the MIME type; pixels are not decoded or validated.
    Provider image limits apply. These guards share the file tools' limitations
    concerning concurrent filesystem changes and do not provide an OS sandbox.
    """
    ctx = GuardContext(
        base=resolve_tool_base(base),
        takes_precedence=takes_precedence,
        default_verdict=default_verdict,
        allow=list(allow_rules or []),
        deny=list(deny_rules or []),
        ask=list(ask_rules or []),
        pipe=pipe,
    )
    return build_guarded_tool_chain(
        entry=read_images(ctx).copy(name=READ_IMAGES_TOOL_NAME),
        guard=operation_guard(ctx),
        execute=execute_read_image(ctx),
        continuation=continue_read_images(ctx).copy(
            chain_condition=lambda output: isinstance(output, GuardFilesResult)
        ),
    )
