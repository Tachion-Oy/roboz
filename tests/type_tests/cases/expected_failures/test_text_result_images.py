"""Plain text results do not declare image inputs."""

from roboz.models import Image, Str

Str(value="Chart", images=[Image(url="https://example.com/chart.png")])
