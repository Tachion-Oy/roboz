"""Filesystem sandbox layout and derived permissions for agent compositions."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
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
class _SandboxLayout:
    """Private layout mechanism for a sandbox's named filesystem areas."""

    root: Path
    readonly: str = "readonly"
    shared: str = "shared"
    projects: str = "projects"

    def __post_init__(self) -> None:
        """Reject escaping or overlapping sandbox areas."""
        paths = [self.readonly_dir, self.shared_dir, self.projects_dir]
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            raise ValueError("sandbox areas must not overlap")

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

    def project_dir(self, slug: str) -> Path:
        """Resolve a project identity to a direct child directory."""
        if len(Path(slug).parts) != 1:
            raise ValueError("project slug must be a single folder name")
        return _within(self.projects_dir, slug)


@dataclass
class Sandbox(_SandboxLayout):
    """Configurable filesystem scope, persistence paths, and tool permissions.

    This is a tool-level guard, not an operating-system sandbox. Relative
    persistence paths remain inside the selected project's root, including
    after symlink resolution. External storage requires an explicit absolute
    path. Constructing the sandbox and deriving paths or permissions creates no
    directories; hosts own filesystem preparation. Configure a scope before a
    deployment build. Do not reconfigure the same sandbox concurrently.
    """

    logs: Path = Path("logs")
    snapshots: Path = Path("snapshots")
    memory: Path = Path("memory")
    scope_folder: str | None = field(default=None, kw_only=True)

    @classmethod
    def define(
        cls,
        *,
        readonly: str = "readonly",
        shared: str = "shared",
        projects: str = "projects",
        logs: Path = Path("logs"),
        snapshots: Path = Path("snapshots"),
        memory: Path = Path("memory"),
    ) -> partial[Sandbox]:
        """Return a constructor carrying reusable application layout defaults.

        The application supplies ``root`` when it creates each sandbox and may
        override any recorded layout value. Every call returns a fresh,
        unscoped sandbox.
        """
        return partial(
            cls,
            readonly=readonly,
            shared=shared,
            projects=projects,
            logs=logs,
            snapshots=snapshots,
            memory=memory,
        )

    def __post_init__(self) -> None:
        """Validate layout and project-relative persistence configuration."""
        super().__post_init__()
        self._persistence_dirs(
            self.scope_folder
            if self.scope_folder is not None
            else "__sandbox_validation__"
        )

    def configure_scope(self, folder: str) -> None:
        """Select a direct child of projects_dir without creating directories.

        Permit writes in that folder, require confirmation for shared-area
        writes, and retain read access throughout the sandbox. Other writes
        remain denied. Relative persistence paths use the selected folder.
        Invalid selections leave the previous configuration unchanged.
        """
        self._persistence_dirs(folder)
        self.scope_folder = folder

    def _folder(self, slug: str | None) -> str:
        """Use an explicit folder or require a configured scope."""
        folder = self.scope_folder if slug is None else slug
        if folder is None:
            raise ValueError("sandbox scope is not configured; call configure_scope()")
        return folder

    def project_dir(self, slug: str | None = None) -> Path:
        """Resolve an explicit folder or the configured scope inside projects_dir."""
        return super().project_dir(self._folder(slug))

    def _persistence_dirs(self, slug: str | None = None) -> tuple[Path, Path, Path]:
        """Resolve and validate the persistence locations for one project slug."""
        root = self.project_dir(slug)

        def resolve(path: Path) -> Path:
            return path.resolve() if path.is_absolute() else _within(root, str(path))

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

    def permissions(self, slug: str | None = None) -> PermissionPolicy:
        """Read inside the sandbox, write one project, and confirm shared writes.

        Other writes and all paths outside the sandbox are denied. Deriving
        the tool policy creates no directories and starts no runtime work.
        Configured folder names are literal, including glob metacharacters.
        """
        slug = self._folder(slug)
        self.project_dir(slug)
        writes = {Operation.CREATE, Operation.DELETE}
        project_pattern = escape(f"{self.projects}/{slug}")
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

    def project_logs_dir(self, slug: str | None = None) -> Path:
        """Return the conversation-log root for one project."""
        return self._persistence_dirs(slug)[0]

    def project_snapshots_dir(self, slug: str | None = None) -> Path:
        """Return the conversation snapshot root for one project."""
        return self._persistence_dirs(slug)[1]

    def project_memory_dir(self, slug: str | None = None) -> Path:
        """Return the consolidated memory root for one project."""
        return self._persistence_dirs(slug)[2]

    def artifact_dir(self, slug: str, name: str) -> Path:
        """Resolve an additional artifact folder without creating it."""
        return _within(self.project_dir(slug), name)
