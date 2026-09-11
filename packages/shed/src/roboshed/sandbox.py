"""Filesystem sandbox layout and derived permissions for agent compositions."""

from dataclasses import dataclass, field
from glob import escape
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
class PermissionPolicy:
    """An explicit tool boundary independent of application lifecycle concepts."""

    base: Path
    allow: tuple[PermissionRule, ...] = ()
    deny: tuple[PermissionRule, ...] = ()
    ask: tuple[PermissionRule, ...] = ()
    default_verdict: ActionVerdict = ActionVerdict.deny
    takes_precedence: ActionVerdict = ActionVerdict.deny

    @classmethod
    def local(cls, base: Path) -> "PermissionPolicy":
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


@dataclass
class Sandbox:
    """One configurable filesystem scope and its derived tool boundaries.

    This is a tool-level guard, not an operating-system sandbox. Constructing
    or configuring it creates no directories. Configure a scope before asking
    for project paths or permissions. Relative persistence paths must remain
    inside that project; absolute paths explicitly select external storage.
    """

    root: Path
    readonly: str = "readonly"
    shared: str = "shared"
    projects: str = "projects"
    logs: Path = Path("logs")
    snapshots: Path = Path("snapshots")
    memory: Path = Path("memory")
    scope: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        """Reject invalid layout and any supplied initial scope."""
        paths = [self.readonly_dir, self.shared_dir, self.projects_dir]
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            raise ValueError("sandbox areas must not overlap")
        if self.scope is not None:
            project = self._resolve_project(self.scope)
            self._persistence_paths(project)

    @property
    def resolved_root(self) -> Path:
        """Return the absolute sandbox root."""
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

    def _resolve_project(self, folder: str) -> Path:
        """Resolve one direct child of the projects directory."""
        if len(Path(folder).parts) != 1:
            raise ValueError("scope must be a single folder name")
        return _within(self.projects_dir, folder)

    def configure_scope(self, folder: str) -> None:
        """Select a direct child of projects_dir without creating directories.

        Permit writes in that folder, require confirmation for shared-area
        writes, and retain read access throughout the sandbox. Other writes
        remain denied. Relative persistence paths use the selected folder.
        Invalid selections leave the previous configuration unchanged.
        """
        project = self._resolve_project(folder)
        self._persistence_paths(project)
        self.scope = folder

    def project_dir(self) -> Path:
        """Return the configured project directory."""
        if self.scope is None:
            raise ValueError("sandbox scope is not configured; call configure_scope()")
        return self._resolve_project(self.scope)

    def _persistence_paths(self, project: Path) -> tuple[Path, Path, Path]:
        """Resolve and validate persistence paths for a project."""

        def resolve(path: Path) -> Path:
            return path.resolve() if path.is_absolute() else _within(project, str(path))

        paths = (
            resolve(self.logs),
            resolve(self.snapshots),
            resolve(self.memory),
        )
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            raise ValueError("persistence folders must not overlap")
        return paths

    def permissions(self) -> PermissionPolicy:
        """Read inside the sandbox, write one project, and confirm shared writes.

        Other writes and all paths outside the sandbox are denied. Deriving
        the tool policy creates no directories and starts no runtime work.
        Configured folder names are literal, including glob metacharacters.
        """
        project = self.project_dir()
        writes = {Operation.CREATE, Operation.DELETE}
        project_pattern = escape(str(project.relative_to(self.resolved_root)))
        shared_pattern = escape(self.shared)
        shared = (
            PermissionRule(shared_pattern, writes),
            PermissionRule(f"{shared_pattern}/**", writes),
        )
        return PermissionPolicy(
            base=self.resolved_root,
            allow=(
                PermissionRule("**", {Operation.READ}),
                PermissionRule(project_pattern, writes),
                PermissionRule(f"{project_pattern}/**", writes),
                *shared,
            ),
            ask=shared,
            takes_precedence=ActionVerdict.allow,
        )

    def project_logs_dir(self) -> Path:
        """Return the configured project's conversation-log root."""
        return self._persistence_paths(self.project_dir())[0]

    def project_snapshots_dir(self) -> Path:
        """Return the configured project's conversation snapshot root."""
        return self._persistence_paths(self.project_dir())[1]

    def project_memory_dir(self) -> Path:
        """Return the configured project's consolidated memory root."""
        return self._persistence_paths(self.project_dir())[2]

    def artifact_dir(self, name: str) -> Path:
        """Resolve an additional artifact folder without creating it."""
        return _within(self.project_dir(), name)
