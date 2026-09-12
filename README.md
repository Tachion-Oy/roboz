# Roboz

[![CI](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml/badge.svg)](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

Typed control flow and context management for reliable agentic workflows. Roboz
lets the model decide what needs judgment, then hands the result to ordinary,
typed Python for the steps that should not be probabilistic.

> [!WARNING]
> Roboz is pre-release software requiring Python 3.13 or newer. APIs may change
> before 1.0.

## Why Roboz

**Chain tools instead of prompting through every step.** The model chooses an
active tool; successful output can flow directly into typed, passive tools with
no additional model decision. Conditions can route by output value or type,
external policy, or current state. Branches can converge on a shared successor.
Only active tools enter the model's tool surface, keeping orchestration details
out of the prompt while Python and Pydantic enforce the handoffs.

**Treat context as a lifecycle, not an ever-growing transcript.** Every message
can carry its own truncation policy. Keep an operational result intact while it
is recent, reduce it to a stub later, and remove it from model context when it is
stale. `NO_MESSAGE` hides internal chatter immediately. These policies change
only the view sent to the model; runtime events and persisted messages retain the
full record.

## Install

The PyPI commands below require a published release. For an unpublished checkout,
[build and test the local wheels](docs/build-and-test.md). Maintainers publish
[reviewed package tags directly to PyPI](docs/build-and-test.md#preparing-and-publishing-a-release).

```bash
uv add roboz
```

Or with pip:

```bash
python -m pip install roboz
```

Optional tools and adapters are separate distributions in this repository:
`roboshed`, `roboz-endpoints`, and `roboz-proton-bridge`. Start with the
[installation and add-on guide](docs/addons.md) to build their wheels and compose
agents from capabilities. These companion releases must be
published before their named PyPI installs and convenience extras are usable.

## Quick start

```python
from builtins import input as read_input

import roboz as rz


@rz.tool
def ask_number(input: rz.Empty, messages: list[rz.Message]) -> rz.Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        try:
            reply = read_input("Enter an integer: ")
        except EOFError:  # Keep the example runnable in non-interactive checks.
            reply = "7"
        try:
            return rz.Int(value=int(reply))
        except ValueError:
            print("Please enter a whole number.")


@rz.tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 == 0,
)
def report_even(input: rz.Int, messages: list[rz.Message]) -> rz.Stop:
    """Report that the supplied integer is even."""
    print(f"{input.value} is even.")
    return rz.Stop(value="even")


@rz.tool(
    chained_to=ask_number,
    chain_condition=lambda output: output.value % 2 != 0,
)
def report_odd(input: rz.Int, messages: list[rz.Message]) -> rz.Stop:
    """Report that the supplied integer is odd."""
    print(f"{input.value} is odd.")
    return rz.Stop(value="odd")


agent = rz.Agent(
    name="demo",
    is_agentic=False,
    agent_endpoint=None,
    default_tools=[ask_number],
    tools=[report_even, report_odd],
)

agent.invoke()
```

`ask_number` starts with `Empty`, handles input validation locally, and returns a
typed `Int`. Roboz then evaluates both conditions and runs exactly one passive
successor. The routing is ordinary, testable Python; no model needs to interpret
the reply or choose the next step.

The same program is available in [`examples/quickstart.py`](examples/quickstart.py).
See [`examples/tool_chaining.py`](examples/tool_chaining.py) for conditional
routing and convergence, and
[`examples/message_truncation.py`](examples/message_truncation.py) for a sliding
message-visibility window.

## Primitives

| Primitive | Role |
| --- | --- |
| `rz.Agent` | Owns the tool surface, prompt assembly, invoke loop, and runtime events. |
| `@rz.tool` | Creates an action from typed input, messages, and output models. |
| `rz.Ctx` | Binds keyword configuration and dependency objects without a custom class. |
| `@rz.factory` | Creates a tool whose runtime context declares external dependencies. |
| `rz.Skill` | Packages reusable instructions and optional tools. |
| `rz.Message` | Carries content plus its model-context truncation lifecycle. |
| `roboz.runtime.EventPipe` | Emits lifecycle, message, and runtime events to explicit sinks. |

The package contains agent and LLM primitives, models, runtime and persistence
infrastructure, dependencies, skills, tooling, and foundational control/interaction tools.
`roboz.dependencies` also validates exact dependency registrations and binds
checker callbacks without running them. Shed supplies health probes and scheduling
through `roboshed.dependency_health`.
`roboz.llm.ModelSelector` selects among lazy model endpoints without constructing
clients and can supply the current endpoint to a `DependencyRoute`.
Provider catalogs and SDK integrations, guarded file and CLI tools, and application
integrations belong in companion packages and are not dependencies of Roboz.
Install `roboz-endpoints[openai]` for the initial OpenRouter, Cerebras, and Groq
catalogues; it installs core automatically. See the
[endpoint guide](packages/endpoints/README.md) for model selection and SDK adapters.

`roboz.deployment` supplies recursive `DeployableAgent` definitions and
capability contracts. Put definitions directly in `subagents` or
`background_agents`, append extensions with the explicit add methods, and unpack
`agent, background_agents = definition.build()`. Capabilities declare required
owner attributes and bind with `build(agent, pipe)`. `roboshed` supplies reusable
orchestrator and Librarian constructors, concrete capabilities, memory tools,
and sandbox structure. Import the constructors from `roboshed.agents`; the fixed
lazy RoboSprawl recipe is `roboshed.deployments.robosprawl.RoboSprawl`. Construct
it without inputs, configure it through setters, then call argument-free
`build()` with all required inputs supplied. The recipe owns project context
and persistence; the caller owns invocation.

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
| [`docs/addons.md`](docs/addons.md) | Optional packages, composition, and releases |
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

Context-aware tools use `ctx: rz.Ctx` and bind with `rz.Ctx(prefix="hello")`.
See [tool authoring](docs/tool-authoring.md) and the
[context API migration](docs/context-migration.md).
