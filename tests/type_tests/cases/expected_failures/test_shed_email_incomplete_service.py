"""Email providers must implement every required resource and mailbox operation."""

from roboz.shed.tools.email import EmailService


class Incomplete(EmailService):
    pass


# Expected: reportAbstractUsage; resource identity and email operations are missing.
Incomplete()
