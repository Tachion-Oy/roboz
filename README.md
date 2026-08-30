# Roboz

[![CI](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml/badge.svg)](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

Typed, composable building blocks and essential prebuilt tools for agentic workflows.

> [!WARNING]
> Roboz is a pre-release project. APIs may change before 1.0.

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
        [{"action": "stop", "rationale": "Done", "value": "Hello from Roboz!"}]
    ),
)

result, _messages = agent.invoke()
print(result.value)
```

## Standard library

The top-level `roboz` API contains the core agent, tool, skill, model, and
runtime primitives. `roboz.standard` provides the ready-made standard library:

- sandbox and permission primitives;
- guarded CLI commands and safe shell-script execution;
- guarded `apply_patch` file editing;
- agent-runtime and conversation-memory tools.

Provider-neutral catalog support lives with the LLM primitives. The standard
library includes an explicitly illustrative OpenRouter catalog backed by the
OpenAI SDK:

```python
from roboz.standard.providers import example_openrouter

endpoint = example_openrouter.example__mock_chat_model
```

The placeholder model is documentation, not a production route. Copy the catalog
pattern with a real model id and verified context limit in application-owned code.
The example resolves `OPENROUTER_API_KEY` only when an endpoint is materialized.

Provider SDKs and application-specific integrations beyond OpenAI/OpenRouter are intentionally not part of the lean first release.

## Development

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
uv build
uv run twine check dist/*
```

## License

Roboz is licensed under the [Apache License 2.0](LICENSE). Copyright © 2026 Tachion Oy.
