from roboshed.deployments.robosprawl import DeploymentFactory
from roboshed.workspace import Project
from roboz.llm import EndpointLike


def invalid_recipe(project: Project, *, orchestrator_endpoint: EndpointLike) -> str:
    return "not a configured factory"


DeploymentFactory(invalid_recipe)
