from pathlib import Path
from typing import Literal, assert_type

from roboz.runtime import EventPipe
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.models import ActionVerdict
from roboz.shed.skills import pdf_skill
from roboz.shed.tools import ReadImagesInput, get_read_images_tool
from roboz.shed.tools.pdf import get_pdf_tool
from roboz.skill import Skill
from roboz.tooling import Tool

request = ReadImagesInput(paths=["first.png", "second.jpg"], detail="high")
assert_type(request.paths, list[str])
assert_type(request.detail, Literal["auto", "low", "high"] | None)
policy = PermissionPolicy.local(Path.cwd())
pipe = EventPipe()
options = policy.tool_options(pipe)
assert_type(get_read_images_tool(**options), list[Tool])
assert_type(get_pdf_tool(**options), list[Tool])
assert_type(pdf_skill(**options), Skill)
assert_type(pdf_skill(base=Path.cwd(), default_verdict=ActionVerdict.allow), Skill)
assert_type(get_read_images_tool(base=Path.cwd(), default_verdict=ActionVerdict.deny), list[Tool])
assert_type(get_pdf_tool(base=Path.cwd(), default_verdict=ActionVerdict.deny), list[Tool])
