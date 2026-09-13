"""Monitor deployment resources together with standalone selectable endpoints."""

from collections.abc import Sequence
from typing import assert_type

from roboshed.dependency_health import (
    DependencyCheckResult,
    DependencyHealthMonitor,
    DependencyRecord,
    check_dependency,
)
from roboz import Agent, HasExternalDependencies
from roboz.dependencies import ExternalDependency
from roboz.deployment import DeployableAgent
from roboz.llm import LLMEndpoint, TranscriptionEndpoint


def inspect(
    definition: DeployableAgent,
    endpoint: LLMEndpoint,
    selectable: Sequence[LLMEndpoint],
    transcription: TranscriptionEndpoint,
) -> None:
    definition.set_agent_endpoint(endpoint)
    resources = definition.external_dependencies()
    assert_type(resources, tuple[ExternalDependency, ...])
    source: HasExternalDependencies = definition
    assert_type(source.external_dependencies(), tuple[ExternalDependency, ...])
    assert_type(check_dependency(endpoint), DependencyCheckResult)
    monitor = DependencyHealthMonitor((*resources, *selectable, transcription))
    assert_type(monitor.records(), list[DependencyRecord])
    assert_type(monitor.record(endpoint.dependency_id), DependencyRecord | None)
    assert_type(definition.build(), tuple[Agent, tuple[Agent, ...]])
