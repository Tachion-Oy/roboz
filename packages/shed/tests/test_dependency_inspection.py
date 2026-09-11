import pytest

from roboshed.dependency_health import inspect_dependencies
from roboshed.sandbox import Sandbox
from roboshed.deployments import Deployment
from roboz.deployment import DeployableAgent
from roboz.llm import MockLLMEndpoint


def _definition(name, endpoint=None, subagents=(), background_agents=()):
    return DeployableAgent(
        name=name,
        agent_endpoint=endpoint or MockLLMEndpoint([]),
        system_prompt="Complete the task.",
        subagents=subagents,
        background_agents=background_agents,
    )


def test_inspection_preserves_project_folders_and_cleans_failed_build(tmp_path):
    from dataclasses import replace
    from pathlib import Path
    from roboshed.dependency_health import inspect_dependencies

    sandbox = Sandbox(
        tmp_path / "absent",
        shared="team",
        logs=Path("logs-custom"),
    )
    inspected = []

    def failing(sandbox):
        project_slug = "project"
        inspected.append((sandbox, project_slug))
        sandbox.configure_scope(project_slug)
        sandbox.project_dir().mkdir(parents=True)
        raise RuntimeError("recipe failed")

    with pytest.raises(RuntimeError, match="recipe failed"):
        inspect_dependencies(
            failing,
            sandbox=sandbox,
            registrations=(),
        )
    inspected_sandbox, inspected_slug = inspected[0]
    assert replace(inspected_sandbox, root=sandbox.root, scope=None) == sandbox
    assert inspected_slug == "project"
    assert not inspected_sandbox.resolved_root.exists()
    assert not sandbox.resolved_root.exists()


def test_inspection_infers_registration_from_first_duplicate(tmp_path):
    from roboshed.dependency_health import check_executable
    from roboshed.dependency_health import inspect_dependencies
    from roboz import (
        ExecutableDependency,
        ExternalDependencyKind,
        LazyExternalDependency,
    )

    first = ExecutableDependency("python")
    later = LazyExternalDependency(
        first.dependency_id,
        ExternalDependencyKind.NETWORK_SERVICE,
        {},
        lambda: (_ for _ in ()).throw(AssertionError("must not materialize")),
    )

    def configure(sandbox):
        sandbox.configure_scope("project")
        return Deployment(agent=_definition("root"), sandbox=sandbox)

    (bound,) = inspect_dependencies(
        configure,
        sandbox=Sandbox(tmp_path),
        registrations=None,
        additional_dependencies=(first, later),
    )
    assert bound.dependency is first
    assert bound.check is check_executable


def test_inspection_discovers_all_agent_modes_without_materializing(tmp_path):
    from roboz import DependencyRoute, ExternalDependencyKind, LazyExternalDependency
    from roboz.dependencies import DependencyContractError

    resources = [
        LazyExternalDependency(
            f"model:test:{name}",
            ExternalDependencyKind.MODEL_ENDPOINT,
            {},
            lambda: pytest.fail("must not materialize"),
        )
        for name in ("root", "foreground", "background", "nested")
    ]
    sandbox = Sandbox(tmp_path / "configured")
    inspected = []

    def configure(temporary):
        inspected.append(temporary)
        nested = _definition("nested", resources[3])
        background = _definition("background", resources[2], subagents=(nested,))
        root = _definition(
            "root",
            DependencyRoute(lambda: resources[0]),
            subagents=(_definition("foreground", resources[1]),),
            background_agents=(background,),
        )
        temporary.configure_scope("project")
        return Deployment(agent=root, sandbox=temporary)

    bound = inspect_dependencies(configure, sandbox=sandbox, registrations=None)
    assert {item.dependency.dependency_id for item in bound} == {
        item.dependency_id for item in resources
    }
    assert all(
        any(item.dependency is resource for resource in resources) for item in bound
    )
    with pytest.raises(DependencyContractError):
        inspect_dependencies(configure, sandbox=sandbox, registrations=())
    assert sandbox.scope is None
    assert all(not temporary.resolved_root.exists() for temporary in inspected)
    assert not sandbox.resolved_root.exists()
