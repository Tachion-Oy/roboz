"""Workspace structure and explicit permissions for reusable agent compositions."""

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from roboz.runtime import EventPipe

from .models import ActionVerdict, Operation, PermissionRule


class ToolOptions(TypedDict):
    """Permission and runtime keyword inputs accepted by guarded file tools."""

    base: Path
    default_verdict: ActionVerdict
    takes_precedence: ActionVerdict
    allow_rules: list[PermissionRule]
    deny_rules: list[PermissionRule]
    ask_rules: list[PermissionRule]
    pipe: EventPipe


@dataclass(frozen=True)
class WorkspacePermissions:
    """An explicit base and permission policy; no application directory layout."""

    base: Path
    allow: tuple[PermissionRule, ...] = ()
    deny: tuple[PermissionRule, ...] = ()
    ask: tuple[PermissionRule, ...] = ()
    default_verdict: ActionVerdict = ActionVerdict.deny
    takes_precedence: ActionVerdict = ActionVerdict.deny

    @classmethod
    def local(cls, base: Path) -> "WorkspacePermissions":
        """Allow operations inside this root; deny paths outside it.

        These tool guards are not an operating-system sandbox. The demo uses
        read commands and the Python patch tool, without arbitrary shell access.
        """
        root = base.resolve()

        return cls(
            base=root,
            allow=(
                PermissionRule(
                    "**", {Operation.READ, Operation.CREATE, Operation.DELETE}
                ),
            ),
        )

    def tool_options(self, pipe: EventPipe) -> ToolOptions:
        """Bind this permission policy to the owning agent's cancellation pipe."""
        return {
            "base": self.base,
            "default_verdict": self.default_verdict,
            "takes_precedence": self.takes_precedence,
            "allow_rules": list(self.allow),
            "deny_rules": list(self.deny),
            "ask_rules": list(self.ask),
            "pipe": pipe,
        }


def _within(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if (
        not name
        or Path(name).is_absolute()
        or path == root
        or not path.is_relative_to(root)
    ):
        raise ValueError("folder must remain inside its root")
    return path


@dataclass(frozen=True)
class Workspace:
    """Named read-only, shared, and project areas; names do not grant permissions.

    Constructing a workspace has no filesystem side effects. Hosts decide when
    to create directories and which tools may read or write them.
    """

    root: Path
    readonly: str = "readonly"
    shared: str = "shared"
    projects: str = "projects"

    def __post_init__(self) -> None:
        """Reject escaping or overlapping workspace areas."""
        paths = [self.readonly_dir, self.shared_dir, self.projects_dir]
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            raise ValueError("workspace areas must not overlap")

    @property
    def resolved_root(self) -> Path:
        """Return the absolute workspace root."""
        return self.root.resolve()

    @property
    def readonly_dir(self) -> Path:
        """Return the reference-file area."""
        return _within(self.resolved_root, self.readonly)

    @property
    def shared_dir(self) -> Path:
        """Return the shared working area."""
        return _within(self.resolved_root, self.shared)

    @property
    def projects_dir(self) -> Path:
        """Return the parent of project directories."""
        return _within(self.resolved_root, self.projects)

    def project_dir(self, slug: str) -> Path:
        """Resolve a project identity to a direct child directory."""
        if len(Path(slug).parts) != 1:
            raise ValueError("project slug must be a single folder name")
        return _within(self.projects_dir, slug)


@dataclass(frozen=True)
class Project:
    """One workspace project and its persistence locations.

    Persistence paths may be project-relative or explicitly absolute, allowing
    hosts to place logs separately from artifacts. Other artifact paths must
    remain inside the project's root. No directories are created here.
    """

    workspace: Workspace
    slug: str
    logs_dir: Path = Path("logs")
    snapshots_dir: Path = Path("snapshots")
    memory_dir: Path = Path("memory")

    def __post_init__(self) -> None:
        """Validate identity and disjoint persistence locations."""
        paths = [self.logs, self.snapshots, self.memory]
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            raise ValueError("persistence folders must not overlap")

    @property
    def root(self) -> Path:
        """Return this project's artifact root."""
        return self.workspace.project_dir(self.slug)

    @property
    def logs(self) -> Path:
        """Return the conversation-log root, partitioned by agent name."""
        return (self.root / self.logs_dir).resolve()

    @property
    def snapshots(self) -> Path:
        """Return the conversation snapshot root."""
        return (self.root / self.snapshots_dir).resolve()

    @property
    def memory(self) -> Path:
        """Return the consolidated memory root."""
        return (self.root / self.memory_dir).resolve()

    def artifact_dir(self, name: str) -> Path:
        """Resolve an additional artifact folder without creating it."""
        return _within(self.root, name)
