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
The philosophy in RoboZ is that of a a workflow is (mostly) deterministic an only on occasion does one need to call an llm. For example in RoboZ an agent would trigger the "ask Bob if they wan to have lunch" tool and all subsequent steps come by chaining: each tool can be chained to other tools upstream where their outputs are passed down the chain. Each link/edge may introduce a True/False condition, in our case for example if Bob interested in having lunch (with us). If he is not, RoboZ allows for the chain to break and returns back to the default tool, which for an agentic process is usually "ask the llm what to do next". The default mode is that chained tools are not presented to the agent, they are thus *passive* or in other words their role is strictly in forming deterministic workflows and they cannot be invoked.

Chaining not only reduces the llm calls, but it also provides a useful way of introducing a fine-grained guard layer for tool calls. This is in fact precisely how the cli tools and their access policies work in Roboz. For a cli command a chained passive tool evaluates the intent and breaks the chain if policies are violated.


## Start here: Agent with a tool

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

The above [simple example](examples/simple.py) can be run from the root with 

```python
uv run examples/simple.py
```
It creates an agent that returns a random
Simpsons quote. It uses a mock endpoint, with pre-determined replies, so you can run it without API keys.
The main contracts of RoboZ are already visible:

- `@tool` creates an instance of a usable tool for the agent
-  A tool's input and output are typed. Tools also receive the entire message stack
- Callable endpoints are single instances, as a hard rule 
- Different output types impact the dymanics, importantly `Stop` breaks out of the agentic loop
- `agent.invoke()` runs the agent and returns its output `Stop` and messages.

The above does not show the main idea of tool chaining, for that read the following sections.
## The central abstraction

An agent is a loop that calls tools. Everything is defined as a tool: Skills,  background agents, prompting the agent, prompting the user, running nested agents, start up hooks etc. Everything.

**A tool can be trigger in three ways:**
- **Invoked by an agent**: The `prompt_agent` tool asks an LLM what to do next and its `Invoke` output always calls another tool. It is constructed internally for `AgentMode.STEERABLE` and `AgentMode.AUTONOMOUS` agents, but it is still just a tool.
- **By chaining**. After an invoked tool has fired RoboZ checks if a chained tool with a *true* chain condition exists (for more than one *true* condition for a fork you get a runtime error). If yes, the output is passed on and the process repeats until the first broken chain or all chained tools are exhausted
- **As default tools**. Once the tool chain is exhausted or the chain breaks due to a *false* condition the loop returns to the freely defined `default_tool` (there actually can be many, they are all called in sequence).

RoboZ then collapses to the traditional agentic approach as a special case if one just has the `prompt_agent` as the default with no chaining. An `AgentMode.DETERMINISTIC` agent has no `prompt_agent`; its default tools perform tasks directly, allowing deterministic branching through chaining. This is useful for a background agent that performs periodic maintenance work. `AgentMode.STEERABLE` agents may ask the user for input, while `AgentMode.AUTONOMOUS` agents cannot.

## Why is this framework useful?
### Tool Chaining
This may be used to reduce the number of llm calls, leading to a speed increase, lower cost and fewer AI errors. It also provides a useful way of introducing a guard layer for tool calls, which can be used to restrict agentic actions.
### Output Truncation
A Tools output can be hidden from the llm, also partially, and this can start to apply after the message has been shown N times.
### Tool outputs are typed
The contract in RoboZ is that all tool calls and hence in everything that happens is that outputs are typed classes. Agents and tools never exchange raw strings or even JSON, typed classes and validation are present throughout, with designated classes for tasks such as `Invoke` and `Stop`.
### LLm Endpoints are instances
As strict design rule in Roboz, everything that depends on an LLM call must be trivially swappable to another provider or model. This makes changing an agent endpoint trivial and furthermore multi-endpoint functionality, where inside a single agent several endpoints are implemented, quite easy.

The [simple example](examples/simple.py) above does not fully illustrate how these features of RoboZ work. For that see [complex example](examples/complex.py) example below.



## Chains, factories, truncation and many endpoints
![](docs/assets/number-escalation.svg)

In the code example below we illustrate some of the features that make RoboZ different from other frameworks.

**Tool chaining** is usually introduced via the decorator argument `chained_to` which indicates the name of the tool whose output is passed as the input. The input/output contract must be Liskov compatible, i.e the upstream output must be a subclass of the downstream input. A possible chain condition can be passed in via the `chain_condition` callable, which uses by definition the tool's input argument and returns a boolean. The chain condition must evaluate to at most one true condition, but it can evaluate to `false` on all links, in which case you return to the default tool(s). `AgentMode.STEERABLE` and `AgentMode.AUTONOMOUS` construct a `prompt_agent` tool backed by the agent endpoint; `AgentMode.DETERMINISTIC` uses the configured default tools.

The `escalate` is an example of a **tool factory**, which is a simple concept. It accepts a context parameter which is added to the tool's closure and calling the factory with a context argument returns a tool. A very common use case is a tool with an endpoint as a context. In RoboZ all llm **endpoints are instances**, so it is easy to have a specific endpoint for a tool, that is different from that of the agent, below we construct deterministic mock endpoints so that no API keys are required for the examples. Factories have precisely the same chaining arguments in their decorator as a tool.

Another useful feature demonstrated below in the `escalate` factory is **message truncation**. This parameter is present in all output types and allows the tool to decide if the output should be visible in the conversation passed on to the agent. It can truncate partially (show only n chars or just a caller stub) or completely. Importantly, we can choose to start applying the truncation only after the complete message has been shown to the agent n times. Below, we choose to show the message once and then truncate it completely, as useful pattern for example for long tracebacks etc.

All RoboZ tools by definition include the full conversation **messages** as input. They are not intended to be altered in place (although they can be and this is how e.g. *compactification* works), but can be used to alter the behavior of tools in a non-trivial way. For example, a start up hook intended to show the agent some information at the start or performing some initial maintenance can simply be one of the default tools that checks if it has already been called and if it has, does nothing.




```python
from builtins import input as read_input

from roboz import Agent, factory, tool
from roboz.llm import EndpointLike, MockLLMEndpoint, get_completion
from roboz.models import Empty, Int, Message, Role, Stop, Str, filter_messages
from roboz.models.truncation import Severity, Truncation
from roboz.runtime.io import interact_with_user
from roboz.runtime.sinks import CliSink
from roboz.tools import stop


@tool
def ask_number(input: Empty, messages: list[Message]) -> Int:
    """Ask the user for an integer, repeating until the response is valid."""
    while True:
        reply = read_input("Enter an integer: ")
        try:
            return Int(value=int(reply))
        except ValueError:
            print("Please enter an integer :)")


@factory(chained_to=ask_number, chain_condition=lambda x: x.value % 2 == 0)
def escalate(input: Int, messages: list[Message], ctx: EndpointLike) -> Stop | Str:
    """An even number?! Need to check this with HR!"""
    interact_with_user("Careful now, that is pretty spicy!", with_reply=False)
    prompt = f"The user chose {input.value}. Is this too hot to handle?! (y/n)?"
    verdict = get_completion(
        endpoint=ctx, messages=[Message(role=Role.SYSTEM, content=prompt)]
    )
    is_first_escalation = (
        len(filter_messages(caller="escalate", messages=messages)) == 0
    )
    if verdict == "y" and not is_first_escalation:
        return Stop(value="Too much spiciness, need to quit!")
    return Str(
        value="HR gave a pass, but still, let's show this to the agent only once.",
        truncation=Truncation(threshold=1, severity=Severity.REMOVE),
    )


@tool(chained_to=ask_number, chain_condition=lambda x: x.value % 2 != 0)
def give_praise(input: Int, messages: list[Message]) -> Str:
    """We need to give praise for such an erudite approach to the problem."""
    interact_with_user(f"{input.value} a fine and bold choice!", with_reply=False)
    return Str(value=f"{input.value} is good, no biggie.")


agent_endpoint = MockLLMEndpoint(
    responses=[
        *(10 * [{"action": "ask_number", "rationale": "This is my only job"}]),
        {"action": "stop", "rationale": "Enough numbers!", "value": ""},
    ]
)
guard_endpoint = MockLLMEndpoint(responses=10 * ["y"])

agent = Agent(
    name="demo",
    system_prompt=f"Without exception, use the {ask_number.name} tool.",
    event_sinks=[CliSink.default()],
    agent_endpoint=agent_endpoint,
    tools=[ask_number, escalate(guard_endpoint), give_praise, stop],
)
agent.invoke()


```

The above [complex example](examples/complex.py) can be run from the root with

```python
uv run examples/complex.py
```


## Try it

```bash
uv add roboz
```

Or with pip:

```bash
python -m pip install roboz
```

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
