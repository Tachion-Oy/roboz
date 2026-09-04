# Packages and first installation

This repository builds four independently versioned distributions:

| Distribution | Import | Version | Contents |
| --- | --- | --- | --- |
| `roboz` | `roboz` | `0.1.1` (pre-alpha project) | Primitives and existing dependency-free reference tools |
| `roboz-shed` | `roboz_shed` | `0.1.0a1` | Guarded files, patch editing, assistant, neutral email tools |
| `roboz-openai` | `roboz_openai` | `0.1.0a1` | OpenAI-compatible Chat Completions and OpenRouter constructors |
| `roboz-proton-bridge` | `roboz_proton_bridge` | `0.1.0b1` | Proton Bridge mailbox and draft adapter |

Core installs only Pydantic, python-dotenv, and Rich. Companions require core
`>=0.1.1,<0.2.0`: its nested-payload handoff fix is needed by generic guards.
Package maturity labels do not imply every service/environment has been tested.
These are local releases until published.

## Try the first slice

Development requires Python 3.13+ and uv. File CLI tools use Unix executables;
the deterministic demo needs `cat`, while patch editing uses Python only.

```bash
uv sync --locked --dev
uv run roboz-demo --mock --workspace /tmp/roboz-demo-workspace --data-path /tmp/roboz-demo-data
```

The demo creates a uniquely named file, reads it through a guarded tool, verifies
the result, and saves its conversation. Repeating it creates another file.
It needs no credentials or network calls. Full runtime details are in the saved
conversation; the terminal displays a concise result.

## Install built wheels

```bash
uv build --all-packages --out-dir dist/first-slice
uv venv /tmp/roboz-trial --python 3.13
uv pip install --python /tmp/roboz-trial/bin/python dist/first-slice/*.whl
/tmp/roboz-trial/bin/python -I -m roboz_shed.demo --mock --workspace /tmp/roboz-trial-workspace --data-path /tmp/roboz-trial-data
```

Use a new output directory if yours contains wheels from older versions. For
automated staged core-only, Shed-only, Proton, adapter, and extras checks:

```bash
uv run python scripts/check_distributions.py --dist dist/first-slice
```

The verifier installs exact local wheels outside the checkout and may download
third-party dependencies from PyPI. No sibling paths or editable installs are
used. The core source distribution excludes companion source trees.

## Real model and email runs

Set `OPENROUTER_API_KEY` in your shell or secret manager. Choose an available
model and its context limit explicitly:

```bash
uv run roboz-demo --provider openrouter \
  --model YOUR_MODEL_ID --max-context-tokens YOUR_MODEL_CONTEXT_LIMIT \
  --workspace /tmp/roboz-demo-workspace --data-path /tmp/roboz-demo-data \
  --prompt "Read the text files and write a short summary in this workspace."
```

For OpenAI, use `--provider openai` and `OPENAI_API_KEY`. The adapter uses the
core's Chat Completions path; choose a model compatible with its request
parameters. Model catalogs and routing policy remain caller-owned.

Add `--proton` after configuring Bridge as described in the
[Proton guide](../packages/proton-bridge/README.md). `--signature-file PATH`
optionally supplies plain-text signature content; the demo otherwise supplies
an empty signature. No personal signature is built in.

Real model calls can incur charges. Inbox reads mark messages read, and draft
creation writes to the configured mailbox; there is no sending tool. Test email
with a designated mailbox. Library imports and dependency inspection never
connect to a service.

## Compose an application

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboz_shed.assistant import WorkspacePermissions, build_assistant

assistant = build_assistant(
    endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "Done", "value": "Hello!"}
    ]),
    workspace=WorkspacePermissions.local(Path("./workspace")),
)
result, messages = assistant.invoke()
```

The builder supplies read commands and patch editing. Pass additional `tools`
and `skills` explicitly. For capabilities requiring cancellation/events, pass
`tool_builders`: each callable receives the owning agent's `EventPipe` once at
construction and returns tools. No integration registry or automatic activation
is required. The installed demo is a composition example whose explicit flags
import adapters; other Shed library modules do not import them.

`WorkspacePermissions.local` allows operations inside the selected root and
denies resolved paths outside it. These are tool guards, not an OS sandbox.
Arbitrary shell and Git execution are absent from the default assistant.

Another email adapter implements `roboz_shed.tools.email.EmailService`.
`ResolvedFileCommand[Input, Payload]` and `GuardFilesResult[Input, Payload]` carry
typed payloads without registering integration-specific unions. Concrete types
survive in-memory handoffs. When decoding persisted guard JSON, use explicitly
parameterized models so payload types can be reconstructed.

## Independent releases

Extras add dependencies; they do not have separate versions or remove code from
a wheel. Separate distributions provide independent releases.
[Python packaging extras specification](https://packaging.python.org/en/latest/specifications/dependency-specifiers/)

After publication:

```bash
pip install roboz
pip install 'roboz[shed,openai]'
pip install 'roboz[proton-bridge-beta]'
pip install 'roboz-proton-bridge==0.1.0b1'
```

Python's `a`, `b`, and `rc` suffixes denote prereleases. Explicit prerelease
bounds in the extras opt into these initial companion releases. Plain core
installation adds none of them.
[Version specification](https://packaging.python.org/en/latest/specifications/version-specifiers/)

Each package has a changelog and tags `<distribution>-v<version>`, for example
`roboz-proton-bridge-v0.1.0b1`. The release workflow validates the tag, runs checks,
then builds and publishes only that distribution. Initial publication order:
core, Shed/model adapter, then Proton. Convenience extras become usable as their
target packages are published.

Configure each PyPI project's trusted publisher for this repository,
`release.yml`, and GitHub environment `pypi` before publishing. Local builds do
not publish. See the [PyPA publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## Future Hub

The separate Hub repository will own FastAPI, run management, SSE, user-input
handling, project layout, configuration, UI, and a useful default assistant.
It consumes these distributions. Personal signatures, Tero connections, and
business-specific defaults remain configuration or local extensions.

Document backends, packaged UI delivery, other providers, Firecrawl, timesheets,
and Hub migration remain deferred until this installation has been tried.
Codex is excluded.
