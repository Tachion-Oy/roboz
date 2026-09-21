from typing import assert_type

from roboz.models import All
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe
from roboz.tooling import Tool
from roboz.shed.tools import get_compactify_messages_when_needed_tool
from roboz.shed.tools.compactification import CompactifyStatus

compact = get_compactify_messages_when_needed_tool(
    endpoint=MockLLMEndpoint([]),
    pipe=EventPipe(),
    timeout_s=1.0,
)
assert_type(compact, Tool[All, CompactifyStatus])
assert_type(compact(input=All(), messages=[]), CompactifyStatus)
