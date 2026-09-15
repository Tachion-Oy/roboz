<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/roboz-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/roboz-logo-light.svg">
    <img alt="RoboZ" src="docs/assets/roboz-logo-dark.svg" width="560">
  </picture>

  <p><strong>Chain tools. Skip calls.</strong></p>
</div>

RoboZ is a framework for building llm powered agents. The core ingredient is that every tool can may be chained conditionally to a subsequent tool thus allowing easy injection of deterministic flows into agentic processes.

[![CI](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml/badge.svg)](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

> [!WARNING]
> RoboZ is pre-release software requiring Python 3.13 or newer. APIs may change
> before 1.0.

## Basic idea

![](docs/assets/tool-chaining.svg)

### Problems to solve: Context bloat and too many llm calls
Suppose the task we want to achieve is ask our buddy Bob out to lunch and then book a table. For the sake of argument assume that our agent has access to the following MCP servers (Note: this is an example, RoboZ has native Tool primitives):

- Ask Bob what they want
- Find a restaurant
- Book a table.

In the usual approach an agent is presented each MCP server separately in the their system prompt and it must call them one-by-one to complete the task. When the agent is completing the task, at every turn it must choose the correct tool, formulate its output accordingly and absorb the reply into its context, which already must contain the specific instructions on how to use each tool. In addition, at each turn one has to wait for the llm to reply, each reply costs tokens and each reply risks a mistake from the llm.

### Deterministic chains
The philosophy in RoboZ is that the workflow is deterministic an only choosing when to initiate is the agent's job. In RoboZ the agent would trigger the "ask Bob what they want" tool and all subsequent steps come by chaining: each tool is chained to other tools upstream and their output is passed down to the chained tool. Each link/edge may introduce a True/False condition, in this case for example if Bob interested in having lunch (with us). If he is not, RoboZ allows for the chain to break and returns back to the default tool, which for an agentic process is usually "ask the llm what to do next". The default mode is that chained tools are not presented to the agent, they are thus *passive* or in other words their role is strictly in forming deterministic workflows and they cannot be invoked. 

### Message truncation
Lengthy tasks with many tool calls also add many tokens in the context that may not be relevant to the end result. In RoboZ all tools may choose to truncate their message i.e. not show it to the agent in its complete form or only show it in its entirety a few times and then remove it from the agents context entirely, for example.





## Simple example: Agent with a custom tool

```python
from random import choice

from simpsons_quotes import QUOTES

from roboz import Agent, tool
from roboz.llm.endpoints import MockLLMEndpoint
from roboz.models import Empty, Message, Stop


@tool
def get_quote(input: Empty, messages: list[Message]) -> Stop:
    """Return a random Simpsons quote and then stop."""
    return Stop(value=choice(QUOTES))


mock = MockLLMEndpoint(
    responses=[{"action": "get_quote", "rationale": "Need Simpsons quote!"}]
)

agent = Agent(
    name="demo",
    system_prompt="You are a Simpsons quote generator",
    agent_endpoint=mock,
    tools=[get_quote],
)

output, messages_ = agent.invoke()
print(f'"{output.value}"')
```

This [simple example](examples/simple.py) creates an agent that returns a random
Simpsons quote. It uses a mock endpoint, so you can run it without API keys.
The quote list lives in [simpsons_quotes.py](examples/simpsons_quotes.py).

- `@tool` exposes `get_quote` as an action the agent can select.
- `MockLLMEndpoint` supplies a scripted response selecting that action.
- `Stop` returns the quote and ends the agent run.
- `agent.invoke()` runs the agent and returns its output and messages.


## factory closure, endpoint instance and seeing the entire prompt
TBD


## Try it

```bash
uv add roboz
```

Or with pip:

```bash
python -m pip install roboz
```

The complete [quick start](examples/quickstart.py) uses a deterministic mock
endpoint. Bob first chooses sushi; when no seats are available, the typed chain
retries the planner, routes his second choice to pizza, and books—all from one
model-selected entry into the chain and without credentials:

```bash
uv run python examples/quickstart.py
```

For an unpublished checkout, first run `uv sync --locked --dev`. PyPI commands
require a published release; see the [build and test guide](docs/build-and-test.md)
for local wheels.

## Control what reaches the model

Context is a projection, not an ever-growing transcript. Every `Message` can
carry a lifecycle policy: keep an output intact while it is recent, reduce it
to a stub later, and remove it from model context when it is stale.
`NO_MESSAGE` keeps operational chatter out of model context immediately. These
policies affect only what the model sees; runtime events and persisted messages
retain the full record.

The prompt is not assembled behind an opaque stack of framework layers. The
complete generated system prompt is available before invocation:

```python
print(agent.full_system_prompt)
```

## One execution abstraction

Roboz uses tools for work and for orchestration instead of adding a separate
hook mechanism for each new concern.

| Concern | Roboz abstraction |
| --- | --- |
| A model-selectable action | Active `@tool` |
| A deterministic follow-up | Passive chained tool |
| Runtime configuration or dependencies | `@factory` bound to a concrete typed object |
| Startup, preflight, and default flow | `default_tools` |
| Synchronous delegation | A subagent exposed as a named tool |
| Background work | An idempotent background-start tool in the default flow |

Tools remain independently testable callables with typed inputs and outputs.
An agent's dependency view is derived from this same tool graph rather than a
second registry.

## Put models where they belong

Each agent owns its endpoint. A model-backed factory can bind another endpoint
directly, so a planner, specialist, summarizer, or transcription tool does not
have to share a model merely because it belongs to the same workflow.
`LLMEndpointRoute` follows a typed endpoint getter when a tool or agent should
track live model selection; concrete endpoints keep other uses fixed.

For an individual model call inside a factory, bind its endpoint directly and use
`get_completion(endpoint=ctx, messages=messages)` from `roboz.llm`. It returns raw
text by default, ready for the tool to process. Pass `LlmOutputModel=...` for
validated JSON dictionaries and output repair. See the
[completion guide](docs/tool-authoring.md#standalone-llm-backed-tools) and
[model-backed chain example](examples/chain_with_factory.py).

Provider SDKs remain outside core. The `roboz` package supplies the agent,
tooling, model, runtime, persistence, dependency, and deployment primitives;
install integrations only where they are needed.

## Core primitives

| Primitive | Role |
| --- | --- |
| `roboz.Agent` | Owns the active tool surface, prompt, invoke loop, and runtime events. |
| `roboz.tool` | Defines an action with typed input and output models. |
| `roboz.factory` | Binds a concrete typed context or resource to a tool. |
| `roboz.Skill` | Packages reusable instructions and optional tools. |
| `roboz.models.Message` | Carries content and its model-context lifecycle. |
| `roboz.deployment.DeployableAgent` | Composes capabilities, subagents, and background agents. |

## Optional ecosystem

Start with core and add only the integrations the application needs.

| Distribution | Adds |
| --- | --- |
| `roboshed` | Guarded file and CLI tools, memory, compaction, reusable agents, and deployment recipes. |
| `roboz-endpoints` | Lazy model catalogues and SDK adapters for OpenAI-compatible providers. |
| `roboz-proton-bridge` | Proton Bridge email tools. |

See the [add-on guide](docs/addons.md) for installation and composition, and the
[endpoint guide](packages/endpoints/README.md) for model selection and provider
adapters.

## Documentation

| Guide | Start here for |
| --- | --- |
| [Tool authoring](docs/tool-authoring.md) | Chaining, factories, conditions, and typed handoffs |
| [Agent authoring](docs/agent-authoring.md) | Agent composition and prompt policy |
| [Reference](docs/reference.md) | Runtime and API semantics |
| [Message truncation example](examples/message_truncation.py) | Sliding model-context visibility |
| [Testing practices](docs/testing-practices.md) | Deterministic workflow and contract tests |

## Development

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[build and test guide](docs/build-and-test.md) for the complete release gate.
Roboz is typed and ships a PEP 561 `py.typed` marker.

## License

Roboz is licensed under the [Apache License 2.0](LICENSE). Copyright © 2026 Tachion Oy.
