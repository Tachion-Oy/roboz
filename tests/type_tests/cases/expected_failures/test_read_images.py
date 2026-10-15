from pathlib import Path

from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.skills import pdf_skill
from roboz.shed.tools import ReadImagesInput, get_read_images_tool
from roboz.shed.tools.pdf import get_pdf_tool

ReadImagesInput(paths=[123])
ReadImagesInput(paths=["photo.png"], detail="original")
get_read_images_tool(Path.cwd(), ActionVerdict.allow)
options = PermissionPolicy.local(Path.cwd()).tool_options(EventPipe())
get_pdf_tool(options)
pdf_skill(options)
