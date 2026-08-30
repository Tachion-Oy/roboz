"""A project in the three-tier workspace layout and its permission policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .permissions import ActionVerdict, Operation, PermissionRule


class WorkspacePermissions(TypedDict):
    """Permission kwargs accepted by ``get_run_file_command`` / ``get_apply_patch``."""

    base: Path
    allow_rules: list[PermissionRule]
    deny_rules: list[PermissionRule]
    ask_rules: list[PermissionRule]
    takes_precedence: ActionVerdict
    default_verdict: ActionVerdict


@dataclass(frozen=True)
class Project:
    """One named project and its surrounding three-tier workspace.

    Folder names and project subfolders are caller policy. Frozen so the workspace
    or project identity cannot be repointed after being handed to a tool.
    """

    workspace_root: Path
    slug: str
    subdirs: Mapping[str, str]
    readonly_dirname: str
    workspace_dirname: str
    projects_dirname: str
    safe_scripts_dirname: str

    @property
    def base_dir(self) -> Path:
        """The resolved workspace root containing all three tiers."""
        return self.workspace_root.resolve()

    @property
    def readonly_dir(self) -> Path:
        return self.base_dir / self.readonly_dirname

    @property
    def workspace_dir(self) -> Path:
        return self.base_dir / self.workspace_dirname

    @property
    def projects_dir(self) -> Path:
        return self.base_dir / self.projects_dirname

    @property
    def scripts_dir(self) -> Path:
        """Where safe shell scripts live: a folder under the read-only tier."""
        return self.readonly_dir / self.safe_scripts_dirname

    @property
    def allowed_dirnames(self) -> frozenset[str]:
        """Top-level folder names permitted directly under the root."""
        return frozenset(
            {self.readonly_dirname, self.workspace_dirname, self.projects_dirname}
        )

    def project_dir(self, slug: str) -> Path:
        """The writable project folder belonging to ``slug``."""
        return self.projects_dir / slug

    @property
    def root(self) -> Path:
        """This project's writable folder."""
        return self.project_dir(self.slug)

    def validate(self) -> None:
        """Reject unexpected top-level folders directly under the root.

        Only the tier folders (:attr:`allowed_dirnames`) may exist directly under
        the root; a stray folder signals the root is not a clean sandbox. Missing
        tier folders are fine - tools create them at runtime. Files are ignored;
        only directories are checked. A non-existent root is treated as clean.

        Intended to be called once when the hub first opens a workspace root.
        """
        root = self.base_dir
        if not root.exists():
            return
        unexpected = sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and child.name not in self.allowed_dirnames
        )
        if unexpected:
            allowed = ", ".join(sorted(self.allowed_dirnames))
            raise ValueError(
                f"Unexpected folders in workspace root {root}: "
                f"{', '.join(unexpected)}. Only {allowed} are allowed."
            )

    def ensure(self) -> None:
        """Create the shared tiers and this project's folder if absent."""
        for directory in (self.readonly_dir, self.workspace_dir, self.projects_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def workspace_permissions(self) -> WorkspacePermissions:
        """Create the workspace and return this project's tool permissions."""
        self.ensure()
        create_delete = {Operation.CREATE, Operation.DELETE}
        workspace_pat = f"{self.workspace_dirname}/**"
        own_project_pat = f"{self.projects_dirname}/{self.slug}/**"
        return {
            "base": self.base_dir,
            "allow_rules": [
                PermissionRule(pattern="**", operations={Operation.READ}),
                PermissionRule(pattern=own_project_pat, operations=create_delete),
                PermissionRule(pattern=workspace_pat, operations=create_delete),
            ],
            "deny_rules": [],
            "ask_rules": [
                PermissionRule(pattern=workspace_pat, operations=create_delete),
            ],
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.deny,
        }

    def __getattr__(self, name: str) -> Path:
        # Only reached for attributes the dataclass/properties don't define, so a
        # configured subfolder name resolves to its path; everything else is a miss.
        subdirs = self.__dict__.get("subdirs")
        if subdirs is not None and name in subdirs:
            return self.root / subdirs[name]
        raise AttributeError(name)
