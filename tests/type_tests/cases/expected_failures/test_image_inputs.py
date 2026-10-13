"""Image attachments require typed images and supported detail values."""

from roboz.models import Image, Message, Role

Image(url="https://example.com/a.png", detail="invalid")
Message(role=Role.USER, content="Describe", images=["https://example.com/a.png"])
