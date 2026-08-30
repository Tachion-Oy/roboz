"""Consumer-side autocomplete and static-type smoke test.

Open this file in the editor and type ``openrouter.`` to inspect completions.
The import must remain ``roboz.llm.providers``.
"""

from typing import assert_type

from roboz.llm import LLMEndpoint
from roboz.tooling import LazyExternalDependency

from roboz.standard.providers import openrouter

assert_type(
    openrouter.z_ai__glm_5_3_flash,
    LazyExternalDependency[LLMEndpoint],
)
