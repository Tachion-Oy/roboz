from dataclasses import replace

import pytest
from roboshed.capabilities import Compactification
from roboshed.tools.compactification import DEFAULT_THRESHOLD_PERCENT

from roboz import All
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


@pytest.mark.parametrize("threshold", [None, 60.0])
def test_compaction_capability_preserves_tool_default_and_explicit_policy(
    tmp_path, threshold
):
    capability = Compactification()
    if threshold is not None:
        capability = replace(capability, threshold_percent=threshold)
    endpoint = MockLLMEndpoint([], max_context_tokens=1000)
    pipe = EventPipe()
    (tool,) = capability.build(pipe, endpoint).default_tools
    result = tool(input=All(), messages=[])
    expected = DEFAULT_THRESHOLD_PERCENT if threshold is None else threshold
    assert result.to_compaction == str(int(1000 * expected / 100))
    assert result.compactions == 0


def test_compaction_without_any_endpoint_fails_before_starting_work(tmp_path):
    with pytest.raises(ValueError, match="requires an endpoint"):
        Compactification().build(EventPipe(), None)
