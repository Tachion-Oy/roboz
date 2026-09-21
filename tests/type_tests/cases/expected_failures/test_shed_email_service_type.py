"""An unrelated resource does not provide the email service operations."""

from roboz.shed.tools.contexts import EmailContext
from roboz.dependencies import ExecutableDependency

# Expected: reportArgumentType; EmailContext requires EmailService.
EmailContext(
    service=ExecutableDependency("python"),
    is_cancelled=lambda: False,
    timeout_s=30,
    pipe=None,
)
