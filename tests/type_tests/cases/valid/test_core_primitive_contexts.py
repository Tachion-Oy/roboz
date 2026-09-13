"""Built-in core factories retain their concrete context types."""

from typing import assert_type

from roboz import (
    Agent,
    HasExternalDependencies,
    All,
    Empty,
    Factory,
    Invoke,
    PromptUser,
    Str,
    Tool,
    message_user,
    prompt_agent,
    prompt_user,
    prompt_user_at_start,
    run_background_agent,
    run_subagent,
    stop,
)
from roboz.agent import (
    BackgroundAgentContext,
    BackgroundAgentStatus,
    PromptAgentContext,
)
from roboz.dependencies import ExternalDependency
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


assert_type(prompt_user, Factory[PromptUser, Str, str])
assert_type(message_user, Factory[Str, Str, str])
assert_type(prompt_user_at_start, Factory[All, Str, str])
assert_type(prompt_user("No reply"), Tool[PromptUser, Str])
assert_type(run_subagent, Factory[Empty, Str, Agent])
assert_type(
    run_background_agent, Factory[Empty, BackgroundAgentStatus, BackgroundAgentContext]
)
assert_type(prompt_agent, Factory[Empty, Invoke, PromptAgentContext])

agent = Agent(
    name="child",
    system_prompt="Stop.",
    tools=[stop],
    agent_endpoint=MockLLMEndpoint([]),
)
background = BackgroundAgentContext(agent=agent)
prompt = PromptAgentContext(
    endpoint=MockLLMEndpoint([]), active_tools=(stop,), pipe=EventPipe()
)
assert_type(run_subagent(agent), Tool[Empty, Str])
assert_type(run_background_agent(background), Tool[Empty, BackgroundAgentStatus])
assert_type(prompt_agent(prompt), Tool[Empty, Invoke])
assert_type(background.state.checks, int)
assert_type(background.external_dependencies(), tuple[ExternalDependency, ...])
assert_type(prompt.external_dependencies(), tuple[ExternalDependency, ...])


def inspect(context: HasExternalDependencies) -> tuple[ExternalDependency, ...]:
    return context.external_dependencies()


assert_type(inspect(agent), tuple[ExternalDependency, ...])
assert_type(inspect(background), tuple[ExternalDependency, ...])
assert_type(inspect(prompt), tuple[ExternalDependency, ...])
