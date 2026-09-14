# Packages and first installation

This repository builds four independently versioned distributions:

| Distribution | Import | Version | Contents |
| --- | --- | --- | --- |
| `roboz` | `roboz` | `0.1.1` (pre-alpha project) | Agent, tool, skill, control, event, and persistence primitives |
| `roboshed` | `roboshed` | `0.1.0a1` | Agent factories, sandbox policy, capabilities, memory, guarded files, and neutral email tools |
| `roboz-endpoints` | `roboz_endpoints` | `0.1.0a1` | Model catalogues and optional SDK adapters |
| `roboz-proton-bridge` | `roboz_proton_bridge` | `0.1.0b1` | Proton Bridge mailbox and draft adapter |

Core installs only Pydantic, python-dotenv, and Rich. Companions require core
`>=0.1.1,<0.2.0`: its nested-payload handoff fix is needed by generic guards.
Package maturity labels do not imply every service/environment has been tested.
These are local releases until published.

## Development setup

Development requires Python 3.13+ and uv. File command tools use Unix executables;
patch editing uses Python only.

```bash
uv sync --locked --dev
```

## Install built wheels

```bash
uv build --all-packages --out-dir dist/first-slice
uv venv /tmp/roboz-trial --python 3.13
uv pip install --python /tmp/roboz-trial/bin/python dist/first-slice/*.whl
uv pip install --python /tmp/roboz-trial/bin/python \
  'dist/first-slice/roboz_endpoints-0.1.0a1-py3-none-any.whl[openai]'
```

Use a new output directory if yours contains wheels from older versions. For
automated staged core-only, Shed-only, Proton, adapter, and extras checks:

```bash
uv run pytest tests/distributions --no-cov --dist dist/first-slice
```

The verifier installs exact local wheels outside the checkout and may download
third-party dependencies from PyPI. No sibling paths or editable installs are
used. The core source distribution excludes companion source trees.

## Models and email

Install `roboz-endpoints[openai]` to use the initial OpenRouter, Cerebras, and Groq
models. This installs core automatically. The base companion supports catalogue
inspection without an SDK; there is no endpoint-related extra on core.

```python
from roboz_endpoints import openrouter
from roboz.llm import with_openrouter_policy

endpoint = with_openrouter_policy(openrouter.z_ai__glm_5_3, reasoning_effort="low")
```

Pass the endpoint to an agent definition or an individual capability. For manual
routes, configure `roboz_endpoints.adapters.openai_compatible.OpenAICompatibleAdapter`
and use its `chat_endpoint` or `transcription_endpoint` methods. See the
[endpoint guide](../packages/endpoints/README.md)
for all four models, credentials, client ownership, and the breaking migration
from `roboz-openai`. See the [Proton guide](../packages/proton-bridge/README.md)
for email configuration. Applications own model selection and startup.

## Compose an application

```python
from pathlib import Path
from roboz.tools import stop
from roboz.deployment import DeployableAgent, Capability
from roboz.llm import MockLLMEndpoint
from roboshed.capabilities import FileCommands, FileEditing
from roboshed.sandbox import PermissionPolicy

permissions = PermissionPolicy.local(Path("./sandbox"))
agent = DeployableAgent(
    name="file_worker",
    system_prompt="Complete the user's task, then call stop.",
    default_capabilities=(
        Capability(tools=(stop,)),
        FileCommands(),
        FileEditing(),
    ),
)
agent.set_agent_endpoint(MockLLMEndpoint([
    {"action": "stop", "rationale": "done", "value": "Ready."}
]))
agent.set_attributes(permissions=permissions)
agent, background_agents = agent.build()
result, messages = agent.invoke()
```

Compose guarded file capabilities through `DeployableAgent`, or use the
orchestrator and Librarian presets. Each capability owns its tools and skills
and receives the owning configuration and event pipe once per build.

See [agent factories](agent-factories.md) for persistent orchestration, common
sandbox structure, permission injection, and API migration.

Another email adapter implements `roboshed.tools.email.EmailService`.
`ResolvedFileCommand[Input, Payload]` and `GuardFilesResult[Input, Payload]` retain
typed payloads. Decode persisted guard JSON with explicitly parameterized models.

## Independent releases

Extras add dependencies; they do not have separate versions or remove code from
a wheel. Separate distributions provide independent releases.
[Python packaging extras specification](https://packaging.python.org/en/latest/specifications/dependency-specifiers/)

After publication:

```bash
pip install roboz
pip install roboshed 'roboz-endpoints[openai]'
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
handling, project configuration, UI, and the concrete default deployment. Shared workspace
structure and reusable factories belong to `roboshed`, alongside their tools and
capabilities. `roboz` supplies the primitives used to build them.
It consumes these distributions. Personal signatures, Tero connections, and
business-specific defaults remain configuration or local extensions.

Document backends, packaged UI delivery, additional SDK adapters, Firecrawl, timesheets,
and Hub migration remain deferred until this installation has been tried.
Codex is excluded.
