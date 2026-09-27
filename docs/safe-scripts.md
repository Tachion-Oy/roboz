# SafeScripts

`SafeScripts` exposes one `run_shell_script` tool. Omit `script` to list installed
`.sh` entrypoints and their leading comments; supply a relative path to run one.
Scripts receive no arguments or interactive input. Standard output and standard
error share a streamed transcript. The public result includes status, exit code,
output, and a sorted `scripts` list.

Install trusted scripts and their helper files outside every agent-writable path.
Symlink entrypoints and symlink directories are refused. Shed's file permission
policies do not restrict what a script can do: local scripts retain the
application's privileges, and remote scripts retain the serving process's
privileges. The operator owns script review, filesystem permissions, and service
provisioning.

## Local setup

Add the capability to a `DeployableAgent` that has a `Sandbox`. Its resolved root
is the script working directory; that directory must exist before execution.
Constructing the capability creates no directories and launches no processes.
Bash is reported as an executable dependency.

```python
from pathlib import Path

from roboz.deployment import DeployableAgent
from roboz.shed.capabilities import SafeScripts
from roboz.shed.sandbox import Sandbox

agent = DeployableAgent(
    name="operations",
    system_prompt="Use installed scripts for operator tasks.",
    default_capabilities=(SafeScripts(Path("/opt/trusted-scripts")),),
)
agent.set_attributes(sandbox=Sandbox(Path("/srv/workspace")))
# Set an application-owned agent endpoint before building the agent.
```

Local execution requires POSIX process groups and Bash. Imports and local
capability construction remain portable; the socket transport requires Linux.

## Host setup

Run the helper in its own Linux process, with Bash installed and a working
directory that already exists:

```python
from pathlib import Path

from roboz.shed.tools.safe_scripts import serve_scripts

serve_scripts(
    socket_path=Path("/run/user/1000/roboz-scripts/scripts.sock"),
    scripts_dir=Path("/opt/trusted-scripts"),
    cwd=Path("/srv/workspace"),
    timeout_s=300.0,
    max_output_bytes=65_536,
    env_allowlist=("OPERATOR_SETTING",),
)
```

The socket's immediate parent must be an actual directory owned by the serving
user with mode `0700`. The helper creates an absent parent and sets the socket to
`0600`. It refuses an existing socket path, including a stale socket; inspect and
remove stale paths yourself after confirming the previous helper has stopped.

In the client application use `SafeScripts(socket_path=Path(...))` instead of
`scripts_dir`. A remote capability needs no client `Sandbox` or Bash dependency.
Its timeout, output limit, environment allowlist, and working directory belong
to the host; passing local policy overrides to a remote capability is refused.
Expose the socket directory to a containerized client at its configured path,
and arrange compatible user permissions. Script paths and the working directory
are interpreted on the host.

`ScriptSocketDependency.check()` connects, validates the greeting, and closes.
It does not list or run scripts. Deployments report this dependency as
`script_socket:<absolute socket path>`.

## Ownership and cancellation

The public facade retains the existing contexts, models, imports, and factory.
It adapts script output to `ScriptOutputEvent`. Protocol defines the public
models, immutable execution policy, response types, and framing validation.
Client code collects those responses into the public result, whether they arrive
directly from local execution or over a socket.

The server module owns discovery and script execution. Its `Scripts` object is
also used directly for local calls. One execution lock serializes scripts;
contending requests return `busy`, while discovery does not take the lock.
Execution uses `subprocess.Popen` and an `ExitStack`: cleanup terminates the
process group, reaps the subprocess, then releases the lock before delivering
completion. Cleanup also runs when the consumer closes the response stream.

Python's `socketserver.ThreadingUnixStreamServer` owns the listener and connection
threads. Each handler runs its request and sends responses directly. There is no
second worker or outgoing queue. A slow reader applies backpressure directly to
script output; a send that exceeds five seconds closes the stream and cancels
execution. Disconnect, extra client input, and shutdown are checked during
traversal, description reads, and subprocess polling.

Client cancellation closes the connection and propagates the cancellation
exception. Event-sink exceptions propagate separately from transport failures.
Local and remote responses use the same collector, which closes its input stream
if a callback raises. Only the collector retains the transcript; diagnostics
remain separate until public result assembly.

SIGINT or SIGTERM stops acceptance, requests cancellation, joins connection
handlers, restores the original signal handlers, and removes the socket only if
its device and inode still match the socket created by the service. Process-group
cleanup sends SIGTERM, allows two seconds for exit, then sends SIGKILL if needed.
It also runs after successful execution to stop remaining background children.
Children that deliberately leave the process group are outside this mechanism.

## Limits and result handling

| Limit | Value | Owner |
| --- | --- | --- |
| Default operation timeout | 300 seconds | Execution policy / server |
| Default combined output | 65,536 raw bytes | Execution policy / server |
| Raw output read | 4,096 bytes | Server |
| Description | 512 characters | Server |
| Cancellation/deadline polling | 0.1 seconds | Server and transport |
| Process-group grace period | 2 seconds | Server |
| Terminal diagnostic | 4,096 characters | Protocol / client |
| Frame, including newline | 524,288 bytes | Protocol encoder / decoder |
| Greeting and request exchange | 3 seconds | Client / session |
| Concurrent connections | 16 | Service |
| Frame send | 5 seconds | Session |
| Cleanup and delivery allowance | 10 seconds beyond operation timeout | Client |

A frame send uses the socket timeout; shutdown may wait for that bounded send to
finish before the handler exits. Description reads check cancellation between
4,096-character fragments and hold one logical line at a time.

Output is decoded as UTF-8 with replacement for invalid bytes. Remote clients
allow up to three times the advertised raw-byte limit in decoded UTF-8 bytes.
Only script output emits output events. A terminal diagnostic is appended to the
public transcript on a new line without duplicating received output. Discovery
entries are streamed separately, then collected and sorted, so large catalogues
do not have a single-frame ceiling.

A transport failure returns `failed`, keeps received output and entries, and
appends a bounded diagnostic. If sending the request was attempted, the diagnostic
states that the execution outcome is unknown: the host may have acted before the
connection failed. There is no automatic retry. Protocol rejections, delivery
failures, and unexpected execution exceptions are logged by the host. A broken or
partially delivered frame is followed by connection closure, never another frame.

## Protocol upgrades

The private protocol is version **2**, with explicit greeting, request,
output-chunk, script-entry, and completion messages. Each connection supports
one request, or a greeting-only health check. JSON frames are newline delimited;
message kinds, field types, extra fields, and frame limits are validated.
Compatibility depends on the protocol version. The package version in the
greeting is informational, so different RoboZ releases can communicate when
their protocol versions match.

Upgrade client and helper together from protocol 1. Version 2 has no version-1
fallback, and a client rejects an incompatible greeting before sending any
execution request. Restart the helper with the upgraded environment and verify
its greeting health check before enabling client use.
