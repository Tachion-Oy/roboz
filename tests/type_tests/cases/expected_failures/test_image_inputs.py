"""Image attachments and capability flags retain their public types."""

from roboz.endpoints.specs import ChatModelSpec
from roboz.models import Image, Message, Role

Message(role=Role.USER, content="Describe", images=["https://example.com/a.png"])
Image(url="https://example.com/a.png", detail="unsupported")
ChatModelSpec("test", 123, accepts_images="yes")
