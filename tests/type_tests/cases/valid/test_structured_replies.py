from collections.abc import Callable
from typing import assert_type

from roboz.models import AttachmentMetadata, Image, Message
from roboz.runtime import StructuredUserIO, UserReply, default_event_sinks


def accept_reply(host: StructuredUserIO, capability: Callable[[], bool]) -> None:
    reply = host.request_reply("Question", accepts_images=capability)
    assert_type(reply, UserReply | None)
    if reply is not None:
        assert_type(reply.content, str)
        assert_type(reply.images, list[Image])
        assert_type(reply.attachments, list[AttachmentMetadata])


def project(message: Message) -> Message:
    return message.model_copy(update={"images": []})


default_event_sinks(persistence_message_projection=project)
