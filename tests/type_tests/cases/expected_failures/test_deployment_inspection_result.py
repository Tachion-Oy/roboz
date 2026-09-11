from pathlib import Path

from roboshed.dependency_health import inspect_dependencies
from roboshed.sandbox import Sandbox


def invalid_configuration(sandbox: Sandbox) -> str:
    return "not a deployment"


inspect_dependencies(
    invalid_configuration,
    sandbox=Sandbox(Path("unused")),
    registrations=(),
)
