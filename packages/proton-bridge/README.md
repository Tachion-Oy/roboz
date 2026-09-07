# roboz-proton-bridge

Optional Proton Mail Bridge adapter for Roboz. Version `0.1.0b1` is beta.
This is a port of the existing mailbox implementation with independent
packaging; live account behavior must be checked in your own Bridge setup.

Requires Roboz, Shed, Pydantic, and pydantic-settings. It installs no model,
document, web-search, or application SDK. Proton Bridge itself must already be
installed, running, and configured separately.

## Configuration

Supply `ProtonBridgeSettings` explicitly or set these environment variables:

| Variable suffix (prefix `ROBOZ_PROTON_BRIDGE_`) | Meaning |
| --- | --- |
| `IMAP_HOST` | Bridge's IMAP hostname, typically a loopback address |
| `IMAP_PORT` | Port shown by Bridge |
| `TLS_MODE` | `ssl` or `starttls`, matching Bridge |
| `ACCOUNT_ADDRESS` | Your mailbox address |
| `USERNAME` | Bridge IMAP username |
| `PASSWORD` | Bridge IMAP password, not your account password |
| `CA_FILE` | Optional certificate authority file |
| `CERTIFICATE_SHA256` | Optional pinned server certificate fingerprint |

Use the certificate/trust settings appropriate to your Bridge instance. The
adapter verifies either CA trust or the explicitly pinned certificate before
authenticating. No configuration is read on import.
The old Hub-specific environment prefix is not used. See `.env.example` for
names; load it into the environment yourself rather than committing credentials.

## Compose with neutral tools

```python
from pathlib import Path
from roboz_proton_bridge import ProtonBridgeEmailService, ProtonBridgeSettings
from roboshed.models import ActionVerdict, Operation, PermissionRule
from roboshed.tools.email import EmailSignature, get_work_with_email

service = ProtonBridgeEmailService(
    settings=ProtonBridgeSettings.from_environment(),
    signature=EmailSignature(plain_text="", html=""),
)
tools = get_work_with_email(
    service=service,
    base=Path("./workspace").resolve(),
    default_verdict=ActionVerdict.deny,
    allow_rules=[PermissionRule("**", {Operation.READ, Operation.CREATE, Operation.DELETE})],
)
```

Pass a caller-owned signature for draft creation (an explicit empty signature is
valid). The adapter contains no personal signature or branded assets. With an
assistant, build these tools in an agent capability and pass its owning pipe plus
`is_cancelled=lambda: pipe.cancelled` to bind cancellation.

Capabilities: metadata search, bounded reads, attachment download, drafts, and
threaded reply drafts. Reading an Inbox message marks it read. Drafts/Sent reads
are read-only. There is no send operation. Attachments follow file permissions;
operations retain cancellation, timeouts, bounds, and safe provider errors.

The existing `X-Peffa-Request-Id` mail header is retained for draft idempotency
compatibility. It is a wire identifier, not an application dependency.

## Testing

The ported fake-IMAP tests exercise TLS configuration, MIME/signature/attachment
handling, draft idempotency, mailbox references, search, reads, and replies.
They do not prove live Bridge connectivity. A real smoke test should use a
designated mailbox and explicitly check the resulting read flags/drafts. No
live email calls are part of installation or CI.
