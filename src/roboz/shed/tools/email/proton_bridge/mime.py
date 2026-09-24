"""RFC-compliant MIME construction for Proton Bridge drafts."""

from __future__ import annotations

import mimetypes
import re
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import cast

from ..contracts import (
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailInlineImage,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailSignature,
)
from .models import ReplySourceHeaders

MAX_REFERENCES_CHARS = 8_000


def build_draft_message(
    request: EmailDraftRequest,
    *,
    default_from_address: str,
    signature: EmailSignature | None,
) -> bytes:
    """Serialize a draft, optionally with signature and file attachments."""
    return _draft_message(request, default_from_address, signature).as_bytes()


def _draft_message(
    request: EmailDraftRequest,
    default_from_address: str,
    signature: EmailSignature | None,
    *,
    source: ReplySourceHeaders | None = None,
) -> EmailMessage:
    from_address = request.from_address or default_from_address
    if not from_address:
        raise EmailProviderError("A sender address is required to create a draft")

    message = EmailMessage()
    message["From"] = from_address
    message["To"] = ", ".join(request.to)
    if request.cc:
        message["Cc"] = ", ".join(request.cc)
    if request.bcc:
        message["Bcc"] = ", ".join(request.bcc)
    message["Subject"] = request.subject
    if request.reply_to:
        message["Reply-To"] = request.reply_to
    if request.client_request_id:
        message["X-Roboz-Request-Id"] = request.client_request_id
    _set_signed_content(message, request.body_text, signature, source)
    for attachment in request.attachments:
        _attach_file(message, attachment)
    return message


def build_reply_draft_message(
    request: EmailReplyDraftRequest,
    source: ReplySourceHeaders,
    *,
    default_from_address: str,
    signature: EmailSignature | None,
) -> bytes:
    """Serialize a threaded reply draft with optional signature."""
    draft = EmailDraftRequest(
        to=source.to,
        cc=source.cc,
        bcc=(),
        subject=reply_subject(source.subject),
        body_text=request.body_text,
        from_address=request.from_address,
        reply_to=None,
        client_request_id=request.client_request_id,
        attachments=request.attachments,
    )
    message = _draft_message(draft, default_from_address, signature, source=source)
    message["In-Reply-To"] = source.message_id
    message["References"] = _bounded_references(source.references)
    return message.as_bytes()


def _set_signed_content(
    message: EmailMessage,
    body_text: str,
    signature: EmailSignature | None,
    source: ReplySourceHeaders | None,
) -> None:
    plain = _signed_plain_text(body_text, signature) if signature else body_text
    html = _signed_html(body_text, signature) if signature else None
    if source is not None and source.quoted_body is not None:
        attribution = (
            f"On {source.sent_at}, {source.sender} wrote:"
            if source.sent_at
            else f"{source.sender} wrote:"
        )
        quoted = "\n".join(
            f"> {line}" if line else ">" for line in source.quoted_body.splitlines()
        )
        plain = f"{plain.rstrip()}\n\n{attribution}\n{quoted}"
        if html is not None:
            html += (
                '<div class="protonmail_quote">' + escape(attribution) + "<br>"
                '<blockquote class="protonmail_quote" type="cite">'
                + _html_lines(source.quoted_body)
                + "</blockquote></div>"
            )
    message.set_content(plain, charset="utf-8")
    if html is not None and signature is not None:
        message.add_alternative(html, subtype="html", charset="utf-8")
        _add_inline_image(message, signature.inline_image, signature.html)


def _signed_plain_text(body_text: str, signature: EmailSignature) -> str:
    plain_text = signature.plain_text.strip()
    if not plain_text or "\x00" in plain_text:
        raise EmailProviderError("The configured email signature text is invalid")
    return f"{body_text.rstrip()}\n\n{plain_text}"


def _signed_html(body_text: str, signature: EmailSignature) -> str:
    html = signature.html.strip()
    if not html or "\x00" in html:
        raise EmailProviderError("The configured email signature HTML is invalid")
    return (
        f"<div>{_html_lines(body_text.rstrip())}</div>\n"
        '<div class="roboz_signature" style="margin-top: 1em">'
        f"{html}</div>"
    )


def _add_inline_image(
    message: EmailMessage,
    image: EmailInlineImage | None,
    signature_html: str,
) -> None:
    if image is None:
        return
    maintype, separator, subtype = image.content_type.partition("/")
    if (
        maintype != "image"
        or not separator
        or not subtype
        or not image.data
        or not image.filename.strip()
        or not image.content_id.strip()
        or any(character in image.content_id for character in "<>\r\n\t ")
    ):
        raise EmailProviderError("The configured email signature image is invalid")
    if f"cid:{image.content_id}" not in signature_html:
        raise EmailProviderError(
            "The configured email signature HTML does not reference its image"
        )
    payload = message.get_payload()
    if not isinstance(payload, list) or not payload:
        raise EmailProviderError("Unable to construct the email signature image")
    html_part = cast(EmailMessage, payload[-1])
    html_part.add_related(
        image.data,
        maintype=maintype,
        subtype=subtype,
        cid=f"<{image.content_id}>",
        disposition="inline",
        filename=image.filename,
    )


def _html_lines(value: str) -> str:
    return escape(value).replace("\n", "<br>\n")


def reply_subject(subject: str) -> str:
    """Add a single reply prefix to the bounded subject."""
    normalized = " ".join(subject.split()) or "(no subject)"
    if re.match(r"(?i)^re\s*:", normalized):
        return normalized[:998]
    return f"Re: {normalized}"[:998]


def _bounded_references(references: tuple[str, ...]) -> str:
    retained: list[str] = []
    length = 0
    for message_id in reversed(references):
        added = len(message_id) + (1 if retained else 0)
        if retained and length + added > MAX_REFERENCES_CHARS:
            break
        retained.append(message_id)
        length += added
    retained.reverse()
    if not retained:
        raise EmailProviderError("A parent Message-ID is required to create a reply")
    return " ".join(retained)


def _attach_file(message: EmailMessage, attachment: EmailDraftAttachment) -> None:
    path = Path(attachment.path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EmailProviderError(
            f"Unable to read attachment {attachment.filename!r}"
        ) from exc
    maintype, subtype = _content_type(attachment.filename)
    message.add_attachment(
        data, maintype=maintype, subtype=subtype, filename=attachment.filename
    )


def _content_type(filename: str) -> tuple[str, str]:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed is None:
        return "application", "octet-stream"
    maintype, _, subtype = guessed.partition("/")
    if not maintype or not subtype:
        return "application", "octet-stream"
    return maintype, subtype
