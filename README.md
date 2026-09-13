<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/roboz-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/roboz-logo-light.svg">
    <img alt="RoboZ logo" src="docs/assets/roboz-logo-light.svg" width="180">
  </picture>
</p>

<h1 align="center">RoboZ</h1>

<p align="center"><strong>Chain tools. Skip calls.</strong></p>

RoboZ is a framework for building llm powered agents. The core ingredient is that every tool can may be chained conditionally to a subsequent tool thus allowing easy injection of deterministic flows into agentic processes.

[![CI](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml/badge.svg)](https://github.com/Tachion-Oy/roboz/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

> [!WARNING]
> RoboZ is pre-release software requiring Python 3.13 or newer. APIs may change
> before 1.0.

## Basic idea

![The usual agent loop sends every lunch-planning step back through the agent. A Roboz chain returns to the agent when Bob wants no lunch, passes any cuisine into one parameterized restaurant search, and retries the plan directly when no seats are available.](docs/assets/tool-chaining.svg)

### Problems in agents: Context bloat and excessive back-and-forth
Suppose the task we want to achieve is ask our buddy Bob out to lunch and then book a table. For the sake of argument assume that our agent has access to the following MCP servers (Note: this is an example, RoboZ has native Tool primitives):

- Ask Bob what they want
- Find a restaurant
- Book a table.

In the usual approach an agent is presented each MCP server separately in the their system prompt and it must call them one-by-one to complete the task. When the agent is completing the task, at every turn it must choose the correct tool, formulate its output accordingly and absorb the reply into its context, which already must contain the specific instructions on how to use each tool. In addition, at each turn one has to wait for the llm to reply, each reply costs tokens and each reply risks a mistake from the llm.

### Deterministic chains
The philosophy in RoboZ is that the workflow is deterministic an only choosing when to initiate is the agent's job. In RoboZ the agent would trigger the "ask Bob what they want" tool and all subsequent steps come by chaining: each tool is chained to other tools upstream and their output is passed down to the chained tool. Each link/edge may introduce a True/False condition, in this case for example if Bob interested in having lunch (with us). If he is not, RoboZ allows for the chain to break and returns back to the default tool, which for an agentic process is usually "ask the llm what to do next". The default mode is that chained tools are not presented to the agent, they are thus *passive* or in other words their role is strictly in forming deterministic workflows and they cannot be invoked. 

### Message truncation
Lengthy tasks with many tool calls also add many tokens in the context that may not be relevant to the end result. In RoboZ all tools may choose to truncate their message i.e. not show it to the agent in its complete form or only show it in its entirety a few times and then remove it from the agents context entirely, for example.





## Code example
TBD


```python
import roboz as rz


class LunchPreference(rz.Empty):
    cuisine: str | None


class Restaurant(rz.Empty):
    name: str
    seats_available: bool


class Booking(rz.Empty):
    confirmation: str


@rz.tool
def plan_lunch_with_bob(
    input: rz.Empty, messages: list[rz.Message]
) -> LunchPreference:
    ...


@rz.tool
def retry_plan_lunch_with_bob(
    input: Restaurant, messages: list[rz.Message]
) -> LunchPreference:
    ...


@rz.tool(
    chained_to=[plan_lunch_with_bob, retry_plan_lunch_with_bob],
    chain_condition=lambda output: (
        isinstance(output, LunchPreference)
        and output.cuisine is not None
    ),
)
def find_restaurant(
    input: LunchPreference, messages: list[rz.Message]
) -> Restaurant:
    ...


retry_plan_lunch_with_bob.chain(
    chained_to=find_restaurant,
    chain_condition=lambda output: (
        isinstance(output, Restaurant) and not output.seats_available
    ),
)


@rz.tool(
    chained_to=find_restaurant,
    chain_condition=lambda output: (
        isinstance(output, Restaurant) and output.seats_available
    ),
)
def book_a_table(input: Restaurant, messages: list[rz.Message]) -> Booking:
    ...
```

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

Provider SDKs remain outside core. The `roboz` package supplies the agent,
tooling, model, runtime, persistence, dependency, and deployment primitives;
install integrations only where they are needed.

## Core primitives

| Primitive | Role |
| --- | --- |
| `rz.Agent` | Owns the active tool surface, prompt, invoke loop, and runtime events. |
| `@rz.tool` | Defines an action with typed input and output models. |
| `@rz.factory` | Binds a concrete typed context or resource to a tool. |
| `rz.Skill` | Packages reusable instructions and optional tools. |
| `rz.Message` | Carries content and its model-context lifecycle. |
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
