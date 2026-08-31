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

The package contains only primitives: agent and LLM abstractions, models, runtime
and persistence infrastructure, skills, tooling, and foundational control and
interaction tools. Provider catalogs, provider SDK integrations, guarded file and
CLI tools, composite agents, and application integrations belong in companion
packages and are not dependencies of Roboz.

## Documentation

| Guide | Contents |
| --- | --- |
| [`docs/reference.md`](docs/reference.md) | Concise API and runtime reference |
| [`docs/agent-authoring.md`](docs/agent-authoring.md) | Agent composition and prompt policy |
| [`docs/tool-authoring.md`](docs/tool-authoring.md) | Tools, factories, dependencies, and chaining |
| [`docs/testing-practices.md`](docs/testing-practices.md) | Test design and review expectations |
| [`docs/build-and-test.md`](docs/build-and-test.md) | Local setup and CI-equivalent validation |

## Development

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
uv build
```

Roboz is typed and ships a PEP 561 `py.typed` marker.

## License

Roboz is licensed under the [Apache License 2.0](LICENSE). Copyright © 2026 Tachion Oy.
