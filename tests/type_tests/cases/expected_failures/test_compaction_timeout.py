from roboz.llm import MockLLMEndpoint
from roboshed.tools import get_compactify_messages_when_needed_tool

get_compactify_messages_when_needed_tool(endpoint=MockLLMEndpoint([]), timeout_s="60")
