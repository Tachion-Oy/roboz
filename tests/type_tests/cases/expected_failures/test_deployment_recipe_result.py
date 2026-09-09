from roboshed.deployments.robosprawl import DeploymentFactory
from roboshed.sandbox import Sandbox
from roboz.llm import EndpointLike


def invalid_recipe(
    sandbox: Sandbox, project_slug: str, *, orchestrator_endpoint: EndpointLike
) -> str:
    return "not a configured factory"


DeploymentFactory(invalid_recipe)
