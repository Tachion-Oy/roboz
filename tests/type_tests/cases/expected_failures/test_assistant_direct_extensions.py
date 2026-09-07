from roboshed.assistant import build_assistant
from roboshed.workspace import Project
from roboz.llm import EndpointLike


def configure(project: Project, endpoint: EndpointLike) -> None:
    build_assistant(project=project, endpoint=endpoint, tools=(), skills=())
