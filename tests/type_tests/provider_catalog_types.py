"""Consumer-side autocomplete and static-type smoke test.

Open this file in the editor and type ``example_openrouter.`` to inspect
completions. The import must remain ``roboz.standard.providers``.
"""

from typing import assert_type

from roboz.llm import LLMEndpoint
from roboz.tooling import LazyExternalDependency

from roboz.standard.providers import example_openrouter

assert_type(
    example_openrouter.example__mock_chat_model,
    LazyExternalDependency[LLMEndpoint],
)
