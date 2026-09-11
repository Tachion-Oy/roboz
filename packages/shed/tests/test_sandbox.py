from dataclasses import replace
from pathlib import Path

import pytest
from roboshed.models import ActionVerdict, Operation
from roboshed.sandbox import PermissionPolicy, Sandbox
from roboshed.tools.utils import check_allow_deny_permission

from roboz import Ctx


def test_constructs_unscoped_sandbox_with_layout_configuration(tmp_path):
    sandbox = Sandbox(
        tmp_path,
        shared="workspace",
        logs=Path("conversation_logs"),
        memory=Path("persistent_memory"),
    )

    assert sandbox.root == tmp_path
    assert sandbox.shared == "workspace"
    assert sandbox.logs == Path("conversation_logs")
    assert sandbox.memory == Path("persistent_memory")
    assert sandbox.scope is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("original_scope", [None, "original"])
def test_for_project_preserves_layout_and_keeps_scopes_independent(
    tmp_path, original_scope
):
    sandbox = Sandbox(
        tmp_path / "root",
        readonly="references",
        shared="team",
        projects="jobs",
        logs=Path("conversations"),
        snapshots=Path("summaries"),
        memory=Path("knowledge"),
        scope=original_scope,
    )
    first = sandbox.for_project("one")
    second = sandbox.for_project("two")

    assert first is not sandbox and second is not sandbox and first is not second
    assert sandbox.scope == original_scope
    for scoped, slug in ((first, "one"), (second, "two")):
        project = tmp_path / "root/jobs" / slug
        assert scoped.readonly_dir == tmp_path / "root/references"
        assert scoped.shared_dir == tmp_path / "root/team"
        assert scoped.project_dir() == project
        assert scoped.project_logs_dir() == project / "conversations"
        assert scoped.project_snapshots_dir() == project / "summaries"
        assert scoped.project_memory_dir() == project / "knowledge"
    second.configure_scope("three")
    assert first.project_dir() == tmp_path / "root/jobs/one"
    assert sandbox.scope == original_scope
    assert not sandbox.root.exists()


@pytest.mark.parametrize("slug", ["one", "two"])
def test_for_project_permissions_allow_only_the_selected_project(tmp_path, slug):
    sandbox = Sandbox(tmp_path / "root", projects="jobs")
    policy = sandbox.for_project(slug).permissions()
    for project in ("one", "two"):
        verdict = check_allow_deny_permission(
            location=sandbox.projects_dir / project / "file.txt",
            op_type=Operation.CREATE,
            takes_precedence=policy.takes_precedence,
            allow_rules=policy.allow,
            deny_rules=policy.deny,
            default_verdict=policy.default_verdict,
            base_path=policy.base,
        )
        expected = ActionVerdict.allow if project == slug else ActionVerdict.deny
        assert verdict == expected
    assert sandbox.scope is None
    assert not sandbox.root.exists()


@pytest.mark.parametrize(
    "slug,memory",
    [("", "memory"), ("../escape", "memory"), ("one", "../escape"), ("one", "logs")],
)
def test_for_project_validates_without_changing_source(tmp_path, slug, memory):
    sandbox = Sandbox(tmp_path / "root", memory=Path(memory))
    with pytest.raises(ValueError):
        sandbox.for_project(slug)
    assert sandbox.scope is None
    assert not sandbox.root.exists()


def test_configure_scope_preserves_policy_and_validates_before_mutation(tmp_path):
    sandbox = Sandbox(tmp_path, projects="jobs")
    with pytest.raises(ValueError, match="configure_scope"):
        sandbox.permissions()
    sandbox.configure_scope("one")
    assert sandbox.project_dir() == tmp_path / "jobs/one"
    captured = replace(sandbox)
    for invalid in ("", "..", "../escape", "nested/folder", str(tmp_path)):
        with pytest.raises(ValueError):
            sandbox.configure_scope(invalid)
        assert sandbox.scope == "one"
    sandbox.configure_scope("two")
    assert captured.scope == "one"
    assert sandbox.scope == "two"
    assert not list(tmp_path.iterdir())


def test_sandbox_names_and_persistence_are_independently_configurable(tmp_path):
    sandbox = Sandbox(
        tmp_path / "root",
        readonly="references",
        shared="team",
        projects="jobs",
        logs=tmp_path / "separate-logs",
        snapshots=Path("summaries"),
        memory=Path("knowledge"),
    )
    sandbox.configure_scope("research")
    project_root = sandbox.project_dir()
    assert project_root == tmp_path / "root/jobs/research"
    assert sandbox.project_logs_dir() == tmp_path / "separate-logs"
    assert sandbox.project_snapshots_dir() == project_root / "summaries"
    assert sandbox.artifact_dir("reports/draft") == (
        project_root / "reports/draft"
    )
    assert sandbox.shared_dir == tmp_path / "root/team"
    assert not sandbox.root.exists()
    with pytest.raises(ValueError, match="inside"):
        sandbox.artifact_dir("../escape")
    with pytest.raises(ValueError, match="overlap"):
        replace(sandbox, memory=Path("summaries/nested"))
    with pytest.raises(ValueError, match="overlap"):
        Sandbox(tmp_path, shared="readonly/nested")


@pytest.mark.parametrize(
    ("field", "accessor"),
    [
        ("logs", "project_logs_dir"),
        ("snapshots", "project_snapshots_dir"),
        ("memory", "project_memory_dir"),
    ],
)
def test_sandbox_accepts_nested_relative_persistence_without_creating_dirs(
    tmp_path: Path, field: str, accessor: str
):
    sandbox = Sandbox(tmp_path / "sandbox", **{field: Path("storage/nested")})
    sandbox.configure_scope("research")
    method = getattr(sandbox, accessor)
    assert method() == sandbox.project_dir() / "storage/nested"
    assert not sandbox.root.exists()


@pytest.mark.parametrize("field", ["logs", "snapshots", "memory"])
@pytest.mark.parametrize("path", ["../escape", "nested/../../escape", "."])
def test_sandbox_rejects_relative_persistence_outside_project(
    tmp_path: Path, field: str, path: str
):
    sandbox = Sandbox(tmp_path / "sandbox", **{field: Path(path)})
    with pytest.raises(ValueError, match="inside"):
        sandbox.configure_scope("research")
    assert not tmp_path.joinpath("sandbox").exists()


@pytest.mark.parametrize(
    ("field", "accessor"),
    [
        ("logs", "project_logs_dir"),
        ("snapshots", "project_snapshots_dir"),
        ("memory", "project_memory_dir"),
    ],
)
def test_sandbox_rejects_escaping_persistence_symlink(
    tmp_path: Path, field: str, accessor: str
):
    sandbox = Sandbox(tmp_path / "sandbox", **{field: Path("linked/nested")})
    sandbox.configure_scope("research")
    project_root = sandbox.project_dir()
    project_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        getattr(sandbox, accessor)()
    assert not (outside / "nested").exists()


@pytest.mark.parametrize(
    ("field", "accessor"),
    [
        ("logs", "project_logs_dir"),
        ("snapshots", "project_snapshots_dir"),
        ("memory", "project_memory_dir"),
    ],
)
def test_sandbox_accepts_absolute_external_persistence(
    tmp_path: Path, field: str, accessor: str
):
    outside = tmp_path / "outside"
    sandbox = Sandbox(tmp_path / "sandbox", **{field: outside / "nested/../storage"})
    sandbox.configure_scope("research")
    assert getattr(sandbox, accessor)() == outside / "storage"
    assert not sandbox.root.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    ("field", "accessor"),
    [
        ("logs", "project_logs_dir"),
        ("snapshots", "project_snapshots_dir"),
        ("memory", "project_memory_dir"),
    ],
)
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_sandbox_rejects_overlapping_persistence(
    tmp_path: Path, field: str, accessor: str, absolute: bool, nested: bool
):
    sandbox = Sandbox(tmp_path / "sandbox")
    sandbox.configure_scope("research")
    other = "memory" if field == "logs" else "logs"
    path = Path(other) / "nested" if nested else Path(other)
    if absolute:
        path = sandbox.project_dir() / path
    with pytest.raises(ValueError, match="overlap"):
        changed = replace(sandbox, **{field: path})
        getattr(changed, accessor)()
    assert not sandbox.root.exists()


@pytest.mark.parametrize("project_scoped", [False, True])
def test_policy_denies_escape_and_symlink_target(tmp_path: Path, project_scoped):
    from roboshed.models import ActionVerdict, Operation
    from roboshed.tools.guard import resolve_allow_verdict

    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    sandbox = Sandbox(root)
    if project_scoped:
        sandbox.configure_scope("research")
    policy = (
        sandbox.permissions()
        if project_scoped
        else PermissionPolicy.local(root)
    )
    ctx = Ctx(
        base=policy.base,
        takes_precedence=policy.takes_precedence,
        allow=list(policy.allow),
        deny=[],
        ask=[],
        default_verdict=policy.default_verdict,
    )
    for path in [outside, root / ".." / "outside.txt", root / "link.txt"]:
        assert resolve_allow_verdict(path, Operation.READ, ctx)[0] == ActionVerdict.deny
    assert (
        resolve_allow_verdict(
            (sandbox.project_dir() if project_scoped else root) / "new.txt",
            Operation.CREATE,
            ctx,
        )[0]
        == ActionVerdict.allow
    )


@pytest.mark.parametrize(
    "area,operation,allowed",
    [
        ("references", Operation.READ, True),
        ("team", Operation.READ, True),
        ("work/other", Operation.READ, True),
        ("work/my-project", Operation.CREATE, True),
        ("work/my-project", Operation.DELETE, True),
        ("references", Operation.CREATE, False),
        ("work/other", Operation.DELETE, False),
        ("../outside", Operation.READ, False),
        ("../outside", Operation.CREATE, False),
    ],
)
def test_configured_sandbox_permission_boundaries(tmp_path, area, operation, allowed):
    sandbox = Sandbox(
        tmp_path / "root", readonly="references", shared="team", projects="work"
    )
    sandbox.configure_scope("my-project")
    policy = sandbox.permissions()
    verdict = check_allow_deny_permission(
        location=policy.base / area / "file.txt",
        op_type=operation,
        takes_precedence=policy.takes_precedence,
        allow_rules=policy.allow,
        deny_rules=policy.deny,
        default_verdict=policy.default_verdict,
        base_path=policy.base,
    )
    assert verdict == (ActionVerdict.allow if allowed else ActionVerdict.deny)


@pytest.mark.parametrize(
    "reply,expected", [("yes", ActionVerdict.allow), ("no", ActionVerdict.deny)]
)
def test_shared_writes_require_confirmation(tmp_path, monkeypatch, reply, expected):
    from roboshed.tools import utils

    from roboz.runtime import EventPipe

    sandbox = Sandbox(tmp_path / "root")
    sandbox.configure_scope("my-project")
    policy = sandbox.permissions()
    prompts = []

    def interact(message, *, with_reply):
        assert with_reply
        prompts.append(message)
        return reply

    monkeypatch.setattr(utils, "interact_with_user", interact)
    verdict, _ = utils.check_ask_permission(
        location=sandbox.shared_dir / "file.txt",
        op_type=Operation.CREATE,
        ask_rules=policy.ask,
        base_path=policy.base,
        pipe=EventPipe(),
    )
    assert verdict == expected and len(prompts) == 1


@pytest.mark.parametrize(
    "projects,shared,slug,sibling",
    [
        ("projects", "shared", "*", "projects/other"),
        ("projects", "shared", "**", "projects/other/nested"),
        ("projects", "team?", "mine", "team1"),
        ("work[ab]", "shared", "mine", "worka/mine"),
    ],
)
def test_project_permission_paths_treat_globs_as_literal_names(
    tmp_path, projects, shared, slug, sibling
):
    from roboshed.tools.utils import check_rule

    sandbox = Sandbox(tmp_path, projects=projects, shared=shared)
    sandbox.configure_scope(slug)
    policy = sandbox.permissions()
    for operation in (Operation.CREATE, Operation.DELETE):
        for location in (
            sandbox.project_dir(),
            sandbox.project_dir() / "file.txt",
        ):
            assert check_rule(location, operation, list(policy.allow), policy.base)
            assert not check_rule(location, operation, list(policy.ask), policy.base)
        for location in (sandbox.shared_dir, sandbox.shared_dir / "file.txt"):
            assert check_rule(location, operation, list(policy.allow), policy.base)
            assert check_rule(location, operation, list(policy.ask), policy.base)
        assert not check_rule(
            tmp_path / sibling / "file.txt", operation, list(policy.allow), policy.base
        )
