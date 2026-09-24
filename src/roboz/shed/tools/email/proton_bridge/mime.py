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
from .protocol import EmailHeader

MAX_REFERENCES_CHARS = 8_000


def build_draft_message(
    request: EmailDraftRequest,
    *,
    default_from_address: str,
    signature: EmailSignature | None,
) -> bytes:
    """Serialize a draft, optionally with signature and file attachments."""
    from_address = request.from_address or default_from_address
    if not from_address:
        raise EmailProviderError("A sender address is required to create a draft")

    message = EmailMessage()
    message[EmailHeader.FROM] = from_address
    message[EmailHeader.TO] = ", ".join(request.to)
    if request.cc:
        message[EmailHeader.CC] = ", ".join(request.cc)
    if request.bcc:
        message[EmailHeader.BCC] = ", ".join(request.bcc)
    message[EmailHeader.SUBJECT] = request.subject
    if request.reply_to:
        message[EmailHeader.REPLY_TO] = request.reply_to
    if request.client_request_id:
        message[EmailHeader.REQUEST_ID] = request.client_request_id
    _set_signed_content(message, request.body_text, signature)
    for attachment in request.attachments:
        _attach_file(message, attachment)
    return message.as_bytes()


def build_reply_draft_message(
    request: EmailReplyDraftRequest,
    source: ReplySourceHeaders,
    *,
    default_from_address: str,
    signature: EmailSignature | None,
) -> bytes:
    """Serialize a threaded reply draft with optional signature."""
    from_address = request.from_address or default_from_address
    if not from_address:
        raise EmailProviderError("A sender address is required to create a draft")

    message = EmailMessage()
    message[EmailHeader.FROM] = from_address
    message[EmailHeader.TO] = ", ".join(source.to)
    if source.cc:
        message[EmailHeader.CC] = ", ".join(source.cc)
    message[EmailHeader.SUBJECT] = reply_subject(source.subject)
    message[EmailHeader.IN_REPLY_TO] = source.message_id
    message[EmailHeader.REFERENCES] = _bounded_references(source.references)
    if request.client_request_id:
        message[EmailHeader.REQUEST_ID] = request.client_request_id
    message.set_content(_reply_body(request, source, signature), charset="utf-8")
    if signature is not None:
        message.add_alternative(
            _reply_html(request, source, signature), subtype="html", charset="utf-8"
        )
        _add_inline_image(message, signature.inline_image, signature.html)
    for attachment in request.attachments:
        _attach_file(message, attachment)
    return message.as_bytes()


def _reply_body(
    request: EmailReplyDraftRequest,
    source: ReplySourceHeaders,
    signature: EmailSignature | None,
) -> str:
    reply = _signed_plain_text(request.body_text, signature) if signature else request.body_text
    if not request.include_quoted_original or source.quoted_body is None:
        return reply
    if source.sent_at:
        attribution = f"On {source.sent_at}, {source.sender} wrote:"
    else:
        attribution = f"{source.sender} wrote:"
    quoted = "\n".join(
        f"> {line}" if line else ">" for line in source.quoted_body.splitlines()
    )
    return f"{reply.rstrip()}\n\n{attribution}\n{quoted}"


def _reply_html(
    request: EmailReplyDraftRequest,
    source: ReplySourceHeaders,
    signature: EmailSignature | None,
) -> str:
    reply = _signed_html(request.body_text, signature) if signature else f"<div>{_html_lines(request.body_text)}</div>"
    if not request.include_quoted_original or source.quoted_body is None:
        return reply
    if source.sent_at:
        attribution = f"On {source.sent_at}, {source.sender} wrote:"
    else:
        attribution = f"{source.sender} wrote:"
    quoted = _html_lines(source.quoted_body)
    return (
        f"{reply}\n"
        '<div class="protonmail_quote">\n'
        f"{escape(attribution)}<br>\n"
        '<blockquote class="protonmail_quote" type="cite">'
        f"{quoted}</blockquote>\n"
        "</div>"
    )


def _set_signed_content(
    message: EmailMessage,
    body_text: str,
    signature: EmailSignature | None,
) -> None:
    if signature is None:
        message.set_content(body_text, charset="utf-8")
        return
    message.set_content(_signed_plain_text(body_text, signature), charset="utf-8")
    message.add_alternative(
        _signed_html(body_text, signature), subtype="html", charset="utf-8"
    )
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
