"""CLI / command-output tools' truncation policy.

Plain ``LIGHT`` at every distance (``threshold=0``): each command output is
capped to ``LIGHT_MAX_CHARS`` chars but kept readable at all distances,
so a single large output can never blow up the context, yet recent output stays
available to the agent.

A graded policy (``roboz.runtime.truncation.GRADED``: STUB/REMOVE by distance)
exists but is intentionally NOT used — it needs experimentation before being adopted here.
"""

from roboz.models.truncation import Severity, Truncation, TruncationSpec


def default_cli_truncation() -> TruncationSpec:
    """The CLI/command-output truncation policy: plain LIGHT at every distance."""
    return Truncation(threshold=0, severity=Severity.LIGHT)


__all__ = ["default_cli_truncation"]
