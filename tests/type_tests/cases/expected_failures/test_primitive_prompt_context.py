"""The prompt context requires a chat endpoint."""

from roboz.agent import PromptLLMContext
from roboz.llm import MockTranscriptionEndpoint
from roboz.runtime import EventPipe

# Expected: reportArgumentType; a transcription endpoint cannot prompt an agent.
PromptLLMContext(
    endpoint=MockTranscriptionEndpoint([]), active_tools=(), pipe=EventPipe()
)
