"""Command specification dataclass for guarded CLI tools."""

from __future__ import annotations

import re
from dataclasses import dataclass

from roboz_shed.models import Operation
from roboz_shed.tools.cli_commands.utilities.path_extractors import PathExtractor


@dataclass
class ArgPattern:
    """Regex constraint for argv tokens.

    ``position=None`` applies to all non-path tokens. A numeric position applies
    only to that argv index.
    """

    pattern: re.Pattern[str]
    position: int | None = None


@dataclass
class CmdSpec:
    """Spec for a permitted CLI command."""

    name: str
    path_extractor: PathExtractor | None = None
    allowed_patterns: list[ArgPattern] | None = None
    forbidden_patterns: list[ArgPattern] | None = None
    operation: Operation = Operation.READ
    # When set, source path operands use this operation and destinations use
    # ``operation`` (for example READ->CREATE copies or DELETE->CREATE moves).
    source_operation: Operation | None = None
    requires_ask: bool = False
    hint: str | None = None

    def __post_init__(self) -> None:
        """Normalize name and prevent ambiguous allow+forbid pattern specs."""
        normalized = self.name.strip().lower()
        if not normalized:
            raise ValueError("CmdSpec.name must be non-empty")
        self.name = normalized
        if self.allowed_patterns and self.forbidden_patterns:
            raise ValueError("Only allowed_patterns *or* forbidden_patterns")
        if self.source_operation is not None and self.operation != Operation.CREATE:
            raise ValueError("source_operation requires operation=CREATE")
