# Roboz

[![CI](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml/badge.svg)](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

Typed primitives for composable agentic workflows. The distribution and import
package are both `roboz`; the primary authoring API is available at the package
root.

> [!WARNING]
> Roboz is pre-release software requiring Python 3.13 or newer. APIs may change
> before 1.0.

## Install

```bash
uv add roboz
```

Or with pip:

```bash
python -m pip install roboz
```

Optional tools and adapters are separate distributions in this repository:
`roboz-shed`, `roboz-openai`, and `roboz-proton-bridge`. Start with the
[installation and add-on guide](docs/addons.md) to build their wheels and run
the assistant demo without credentials. These companion releases must be
published before their named PyPI installs and convenience extras are usable.

## Quick start

```python
import roboz as rz
from roboz.llm import MockLLMEndpoint

agent = rz.Agent(
    name="demo",
    system_prompt="Stop and return a greeting.",
    tools=[rz.stop],
    agent_endpoint=MockLLMEndpoint(
        [
            {
                "action": "stop",
                "rationale": "The task is complete.",
                "value": "Hello from Roboz!",
            }
        ]
    ),
)

result, _messages = agent.invoke()
print(result.value)
```

The same program is available in [`examples/quickstart.py`](examples/quickstart.py).

## Primitives

| Primitive | Role |
| --- | --- |
| `rz.Agent` | Owns the tool surface, prompt assembly, invoke loop, and runtime events. |
| `@rz.tool` | Creates an action from typed input, messages, and output models. |
| `@rz.factory` | Creates a tool whose runtime context declares external dependencies. |
| `rz.Skill` | Packages reusable instructions and optional tools. |
| `roboz.runtime.EventPipe` | Emits lifecycle, message, and runtime events to explicit sinks. |

The package contains agent and LLM primitives, models, runtime and persistence
infrastructure, skills, tooling, foundational control/interaction tools, and
dependency-free reference tools such as the Librarian memory pipeline. Provider
catalogs and SDK integrations, guarded file and CLI tools, and application
integrations belong in companion packages and are not dependencies of Roboz.

## Documentation

| Guide | Contents |
| --- | --- |
| [`docs/reference.md`](docs/reference.md) | Concise API and runtime reference |
| [`docs/agent-authoring.md`](docs/agent-authoring.md) | Agent composition and prompt policy |
| [`docs/tool-authoring.md`](docs/tool-authoring.md) | Tools, factories, dependencies, and chaining |
| [`docs/docstrings.md`](docs/docstrings.md) | Python and agent-facing tool docstring conventions |
| [`docs/testing-practices.md`](docs/testing-practices.md) | Test design and review expectations |
| [`docs/build-and-test.md`](docs/build-and-test.md) | Local setup and CI-equivalent validation |
| [`docs/port-parity.md`](docs/port-parity.md) | Audited source-commit parity and deliberate exclusions |
| [`docs/addons.md`](docs/addons.md) | Optional packages, installed demo, composition, and releases |
| [`docs/maintainer-basics.md`](docs/maintainer-basics.md) | Practical changelog, versioning, release, and open-source basics |

## Development

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
uv build
```

Build companions with `uv build --all-packages --out-dir dist/first-slice`.

Roboz is typed and ships a PEP 561 `py.typed` marker.

## License

Roboz is licensed under the [Apache License 2.0](LICENSE). Copyright © 2026 Tachion Oy.
