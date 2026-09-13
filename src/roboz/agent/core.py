"""Agent composition and synchronous tool-execution runtime."""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Any, Literal, Sequence, TypedDict, Unpack

from rich.console import Console
from rich.table import Table

from roboz.agent._execution_context import (
    get_active_agent_stack,
    pop_active_agent,
    push_active_agent,
    validate_agent_name,
)
from roboz.agent._notifications import (
    AUTO_LOAD_SKILLS_BANNER,
    INTERRUPT_PROMPT_TO_USER,
    INTERRUPTED_GENERATION_CONTEXT,
    LLM_PROVIDER_REQUEST_RETRY_PROMPT,
    auto_load_skill_rationale,
)
from roboz.agent._prompts import get_agentic_system_prompt
from roboz.agent._tool_observer import ToolInvocationObserver
from roboz.agent.prompt_agent_tool import PromptAgentContext, prompt_agent
from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    LLMProviderRequestError,
    StopAgent,
)
from roboz.llm.binding import resolve_endpoint
from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    LLMEndpointRoute,
    MockLLMEndpoint,
)
from roboz.models import Empty, Invoke, Message, MessageKind, Role, Stop, Str
from roboz.models._schema import get_constituent_types
from roboz.models._serialization import get_finalized_message
from roboz.runtime.events import EventSink
from roboz.runtime.io import (
    Output,
    bind_output,
    get_bound_output,
    reset_output,
)
from roboz.runtime.persistence import RunStatus
from roboz.runtime.pipe import EventPipe
from roboz.skill.core import Skill
from roboz.tooling.context import HasExternalDependencies
from roboz.tooling.core import Factory, Tool
from roboz.dependencies import (
    ExternalDependency,
    dedupe_external_dependencies,
)
from roboz.tools.interaction import NO_REPLY, prompt_user

logger = getLogger(__name__)


class AgentInputs(TypedDict, total=False):
    """Optional fields for `Agent.copy(**...)`; each key replaces the copied value."""

    interaction_mode: Output | None
    name: str
    description: str
    tools: Sequence[Tool]
    skills: Sequence[Skill]
    system_prompt: str
    auto_loaded_skills: Sequence[Skill] | None
    automatic_tool_prompt: bool
    is_agentic: bool
    default_tools: Sequence[Tool] | None
    custom_prompt_user_tool: Tool | None
    agent_endpoint: EndpointLike | None
    initial_messages: Sequence[Path | str] | None
    event_pipe: EventPipe | None


class Agent(HasExternalDependencies):
    """Agent primitive.

    The agent uses the supplied :class:`~roboz.runtime.pipe.EventPipe`, or creates
    its own per-agent pipe when one is not supplied. ``event_sinks`` is a flat
    list of callbacks attached to a newly created pipe. Hosts compose CLI,
    run-scoped, process-scoped, or agent-specific callbacks before creating the
    agent; Agent does not construct or distinguish those scopes.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        interaction_mode: Output | None = Output.CLI,
        tools: Sequence[Tool | Sequence[Tool]] | None = None,
        skills: Sequence[Skill] | None = None,
        system_prompt: str = "",
        auto_loaded_skills: Sequence[Skill] | None = None,
        automatic_tool_prompt: bool = True,
        is_agentic: bool = True,
        default_tools: Sequence[Tool] | None = None,
        custom_prompt_user_tool: Tool | None = None,
        agent_endpoint: EndpointLike | None,
        initial_messages: Sequence[Path | str] | None = None,
        event_sinks: Sequence[EventSink] | None = None,
        event_pipe: EventPipe | None = None,
    ):
        """Initialize and validate an agent's tools, skills, endpoint, and event pipe."""
        if event_pipe is not None and event_sinks is not None:
            raise ValueError("pass either event_pipe or event_sinks, not both")
        self.name = validate_agent_name(name)
        self.description = description
        self.tools = Tool.to_tool_list(tools)
        self.interaction_mode = (
            interaction_mode
            if interaction_mode is not None
            else get_bound_output(default=Output.CLI)
        )
        self.pipe = (
            event_pipe if event_pipe is not None else EventPipe(event_sinks=event_sinks)
        )
        self.event_sinks = self.pipe.event_sinks
        self._tool_invocations = ToolInvocationObserver(
            agent_name=self.name, pipe=self.pipe
        )
        self.skills = skills
        self.auto_loaded_skills = auto_loaded_skills
        self.system_prompt = system_prompt
        self.automatic_tool_prompt = automatic_tool_prompt
        self.is_agentic = is_agentic
        if self.is_agentic and not self.system_prompt.strip():
            raise ValueError(
                f"Agent '{self.name}' is agentic but has an empty system prompt. "
                "Agentic flows require a non-empty system_prompt."
            )
        self.default_tools = [] if default_tools is None else list(default_tools)
        self.prompt_user_tool = (
            prompt_user(NO_REPLY)
            if custom_prompt_user_tool is None
            else custom_prompt_user_tool
        )
        if agent_endpoint is not None and not isinstance(
            agent_endpoint, (LLMEndpoint, MockLLMEndpoint, LLMEndpointRoute)
        ):
            raise TypeError(
                "agent_endpoint must be an LLMEndpoint, MockLLMEndpoint or LLMEndpointRoute"
            )
        if self.is_agentic and agent_endpoint is None:
            raise ValueError("agentic instances require agent_endpoint")
        self.agent_endpoint = agent_endpoint
        self.initial_messages = initial_messages if initial_messages else []

        self.active_tools: dict[str, Tool] = {}
        self.passive_tools: dict[str, Tool] = {}
        self.messages: list[Message] = []
        self._skills: dict[str, Skill] = {}
        self._auto_loaded_skills: dict[str, Skill] = {}
        self._loaded_skills: set[Skill] = set()

        # Tools
        all_tools = self._append_tools_reducer(
            [self.prompt_user_tool], "tool", self.tools
        )
        all_tools = self._append_tools_reducer(skills, "skill", all_tools)
        all_tools = self._append_tools_reducer(auto_loaded_skills, "skill", all_tools)
        self._init_tools(tools=all_tools, skill=None)

        # Skills
        if skills is not None:
            self._skills = {skill.name: skill for skill in skills}
        if auto_loaded_skills is not None:
            self._auto_loaded_skills = {
                skill.name: skill for skill in auto_loaded_skills
            }

        # System Prompt
        self.full_system_prompt = self._get_full_system_prompt(
            self.automatic_tool_prompt
        )

    @staticmethod
    def _append_tools_reducer(
        addition: list | Sequence | None,
        method: Literal["skill", "tool"],
        current_value: list,
    ) -> list[Tool]:

        if method == "tool":
            if addition is not None:
                return current_value + [a for a in addition if a is not None]
            return current_value
        else:
            if addition is not None:
                return current_value + [s.get_skill_as_tool() for s in addition]
            return current_value

    def _get_full_system_prompt(
        self,
        use_generated_tool_instructions,
    ) -> str:
        if use_generated_tool_instructions and self.system_prompt:
            # Exclude auto-loaded skill wrappers from prompt—they're redundant
            # since those skills are already loaded at invocation start.
            auto_loaded_names = set(self._auto_loaded_skills)
            tools_for_prompt = [
                t for t in self.active_tools.values() if t.name not in auto_loaded_names
            ]
            return get_agentic_system_prompt(
                system_prompt=self.system_prompt,
                tools=tools_for_prompt,
                skills=list(self._skills.values()),
            )
        return self.system_prompt

    def copy(self, **overrides: Unpack[AgentInputs]) -> Agent:
        """Copy this agent configuration while preserving its event pipe by default."""
        old_input: AgentInputs = {
            "interaction_mode": self.interaction_mode,
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "skills": list(self._skills.values()),
            "system_prompt": self.system_prompt,
            "auto_loaded_skills": list(self._auto_loaded_skills.values()),
            "automatic_tool_prompt": self.automatic_tool_prompt,
            "is_agentic": self.is_agentic,
            "default_tools": self.default_tools,
            "custom_prompt_user_tool": self.prompt_user_tool,
            "agent_endpoint": self.agent_endpoint,
            "initial_messages": None
            if not self.initial_messages
            else self.initial_messages,
            "event_pipe": self.pipe,
        }
        return Agent(**(old_input | overrides))

    def _init_default_tool(self):
        if self.is_agentic:
            endpoint = self.agent_endpoint
            if endpoint is None:
                raise RuntimeError("agentic instance has no endpoint")
            self.master_tool = prompt_agent(
                PromptAgentContext(
                    active_tools=tuple(self.active_tools.values()),
                    endpoint=endpoint,
                    pipe=self.pipe,
                )
            )
            return
        if not self.default_tools:
            raise ValueError("No default tools for a non-agentic instance.")
        self.master_tool = self.default_tools[0]

    def _init_tools(self, *, tools: Sequence[Tool], skill: Skill | None):
        for t in self.default_tools:
            if any(
                issubclass(output_type, Invoke)
                for output_type in get_constituent_types(t.OutputModel)
            ):
                raise ValueError(f"Default tool '{t.name}' cannot return Invoke")

        all_tools = set(tools)
        if skill:
            all_tools |= set(skill.tools)

        for t in all_tools:
            if t.chained_to:
                self.passive_tools[t.id] = t
                continue
            self.active_tools[t.id] = t
        self._init_default_tool()
        self._validate_tool_chains(all_tools | {self.master_tool})

    def external_dependencies(
        self,
        *,
        include_lazy_skills: bool = True,
    ) -> tuple[ExternalDependency, ...]:
        """Derive the agent's dependency catalog exclusively from its Tool graph."""
        tools: list[Tool] = [
            self.master_tool,
            self.prompt_user_tool,
            *self.default_tools,
            *self.active_tools.values(),
            *self.passive_tools.values(),
        ]
        if include_lazy_skills:
            for skill in (
                *tuple(self.skills or ()),
                *tuple(self.auto_loaded_skills or ()),
            ):
                tools.extend(skill.tools)

        return dedupe_external_dependencies(
            [resource for tool in tools for resource in tool.external_dependencies()]
        )

    @staticmethod
    def _get_validated_chained_tool_id(
        chained_tool: Tool | Factory, all_tools: dict[str, Tool], tool
    ) -> str:
        ref_id = getattr(chained_tool, "id", None)
        if ref_id not in all_tools:
            logger.error(
                f"Chained tool '{getattr(chained_tool, 'name', 'Unknown')}' (id: {ref_id}) "
                f"for tool '{tool.name}' does not exist in agent tools"
            )
            raise ValueError
        return ref_id

    @staticmethod
    def _validate_link(chained_tool: Tool, tool: Tool):
        types_to_check = get_constituent_types(chained_tool.OutputModel)
        types_to_check = [t for t in types_to_check if not issubclass(t, Stop)]

        for arg in types_to_check:
            if not issubclass(arg, Empty):
                logger.error(
                    f"Chained tool '{chained_tool.name}' must have output that is a subclass of {Empty}, "
                    f"got '{arg.__name__}'"
                )
                raise ValueError

        if types_to_check and not any(
            issubclass(arg, tool.InputModel) for arg in types_to_check
        ):
            logger.error(
                f"Chained tool '{chained_tool.name}' must have output that is a subclass of tool input {tool.InputModel}, "
                f"got {list(types_to_check)}"
            )
            raise ValueError

    def _validate_tool_chains(self, tools: set[Tool]) -> None:
        """This does the heavy lifting for tool chains."""
        active_names = [t.name for t in tools if not t.chained_to]
        if len(active_names) != len(set(active_names)):
            raise ValueError(f"Duplicate tool name {active_names=}")

        all_tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.id in all_tools:
                logger.error(f"Duplicate tool id '{tool.id}' for {tool.name=}")
                raise ValueError
            all_tools[tool.id] = tool

        for t in tools:
            if t.chained_to is None:
                continue

            for chained_tool in t.chained_to:
                chained_id = self._get_validated_chained_tool_id(
                    chained_tool, all_tools, t
                )
                self._validate_link(all_tools[chained_id], t)

    def get_next_tool(
        self, tool: Tool[Empty, Any] | None, output: Invoke | Empty | Stop | Stop
    ) -> Tool:
        """Resolve the unique active, chained, or default tool for an output."""
        chained_tools = []
        if isinstance(output, Invoke):
            self._init_skill(output)
            for t in self.active_tools.values():
                if t.name == output.action:
                    return t
            raise RuntimeError(f"Called tool {output.action} does not exist!")

        for passive in self.passive_tools.values():
            assert passive.chained_to is not None
            for chained_tool in passive.chained_to:
                if (
                    tool
                    and chained_tool.id == tool.id
                    and passive.chain_condition(output)
                ):
                    chained_tools.append(passive)
        if len(chained_tools) == 1:
            return chained_tools[0]
        if len(chained_tools) == 0:
            return self.pop_ephemeral_default_tool()
        logger.error(f"Many True next links in {[c.name for c in chained_tools]}")
        raise RuntimeError

    def pop_ephemeral_default_tool(self):
        """Pop the next per-run default tool, replenishing the sequence as needed."""
        try:
            return self._ephemeral_default_tools.pop(0)
        except IndexError:
            self._ephemeral_default_tools = list(self.default_tools) + [
                self.master_tool
            ]
            return self._ephemeral_default_tools.pop(0)

    def append_and_pipe(self, message: Message):
        """Append a message to conversation state and emit it through the pipe."""
        self.messages += [message]
        self.pipe(message)

    @staticmethod
    def _get_stored_message(location: Path) -> Message | None:
        if not location.is_dir() and location.suffix == ".md":
            return get_finalized_message(
                Str(value=location.read_text()),
                message_kind=MessageKind.STARTUP_CONTEXT,
            )
        if location.is_dir() and list(location.glob("*.md")):
            sorted_location = sorted(location.glob("*.md"), reverse=True)[0]
            return get_finalized_message(
                Str(value=sorted_location.read_text()),
                message_kind=MessageKind.STARTUP_CONTEXT,
            )

    def _init_messages(self):
        self.messages.clear()
        if self.full_system_prompt:
            self.append_and_pipe(
                Message(
                    role=Role.SYSTEM,
                    content=self.full_system_prompt,
                    message_kind=MessageKind.SYSTEM_MESSAGE,
                )
            )

        # Initial messages are attached right after the system
        # message as user messages, before any tool calls
        for i in self.initial_messages:
            if isinstance(i, Path):
                if msg := self._get_stored_message(i):
                    self.append_and_pipe(msg)
                continue
            if isinstance(i, str):
                self.append_and_pipe(
                    Message(
                        role=Role.USER,
                        content=i,
                        message_kind=MessageKind.STARTUP_CONTEXT,
                    )
                )
                continue
            logger.error(
                "Invalid initial message type=%s",
                type(i).__name__,
            )
            raise ValueError

    def add(self, *, tools: list[Tool] | None = None, skill: Skill | None = None):
        """Add tools or a loaded skill and regenerate model-facing instructions."""
        current_tools = list(self.active_tools.values()) + list(
            self.passive_tools.values()
        )

        self._init_tools(
            tools=current_tools + tools if tools else current_tools, skill=skill
        )
        # Regenerate prompt to include newly added tools (e.g. subtask tools)
        if self.automatic_tool_prompt and self.system_prompt:
            self.full_system_prompt = self._get_full_system_prompt(
                self.automatic_tool_prompt
            )

    def _init_skill(self, output: Invoke | Empty | Stop) -> None:
        """Load a skill's tools only after the skill is invoked.

        Enforce declared skill dependency order and avoid loading a skill more
        than once during one run.
        """
        if not isinstance(output, Invoke):
            return
        if (
            skill := (self._skills | self._auto_loaded_skills).get(output.action)
        ) is not None:
            if skill in self._loaded_skills:
                return
            if (
                skill.depends_on is not None
                and skill.depends_on not in self._loaded_skills
            ):
                raise RuntimeError(
                    f"The {skill.name=} depends on {skill.depends_on=}, which has not been loaded."
                )
            self._loaded_skills.add(skill)
            self.add(skill=skill)

    def auto_load_skills(self):
        """Load configured automatic skills and emit their instruction messages."""
        if self._auto_loaded_skills:
            self.append_and_pipe(
                get_finalized_message(
                    Str(value=AUTO_LOAD_SKILLS_BANNER),
                    self.prompt_user_tool,
                    message_kind=MessageKind.AUTO_LOAD_BANNER,
                )
            )
        for skill in self._auto_loaded_skills.values():
            output = Invoke.model_validate(
                {
                    "action": skill.name,
                    "rationale": auto_load_skill_rationale(skill.name),
                }
            )
            self._init_skill(output)
            tool = self.get_next_tool(self.master_tool, output)
            output = tool(output, self.messages)
            self.append_and_pipe(
                get_finalized_message(
                    output,
                    tool,
                    message_kind=MessageKind.AUTO_LOADED_SKILL,
                )
            )

    def _initialize_pipe_for_invoke(self, *, dry_run: bool) -> None:
        """Initialize run metadata from the current endpoint selection.

        Resources own their construction caches; a live reference may select a
        different endpoint for each invocation of the same agent.
        """
        endpoint: LLMEndpoint | MockLLMEndpoint | None = None
        if self.is_agentic:
            configured_endpoint = self.agent_endpoint
            if configured_endpoint is None:
                raise RuntimeError("agentic instance has no endpoint")
            endpoint = resolve_endpoint(configured_endpoint)
        self.pipe.initialize(
            dry_run=dry_run,
            agent_name=self.name,
            agent_description=self.description or None,
            api_name=getattr(endpoint, "api_name", None),
            model_name=getattr(endpoint, "model_name", None),
            max_context_tokens=getattr(endpoint, "max_context_tokens", None),
            temperature=getattr(endpoint, "temperature", None),
            output_format=getattr(endpoint, "output_format", None),
        )

    def _advance_by_step(
        self, tool: Tool | None, output: Invoke | Empty
    ) -> tuple[Tool | None, Invoke | Empty | Stop | Stop]:
        try:
            self.pipe.raise_if_cancelled()
            tool = self.get_next_tool(tool, output)
            self.pipe.raise_if_cancelled()
            tool_output = self._tool_invocations.invoke(tool, output, self.messages)
        except LLMProviderRequestError as error:
            return self._handle_provider_request_error(error)
        except ExternalCallInterruptedError:
            logger.info(
                "Interrupt caught (ExternalCallInterruptedError) in agent '%s' "
                "while running tool '%s'",
                self.name,
                getattr(tool, "name", None),
            )
            return self._handle_interrupt()
        except KeyboardInterrupt:
            if not self.pipe.interrupted:
                logger.info(
                    "KeyboardInterrupt in agent '%s' without interrupt flag set; "
                    "re-raising",
                    self.name,
                )
                raise
            logger.info(
                "KeyboardInterrupt in agent '%s' with interrupt flag set; "
                "handling as interrupt",
                self.name,
            )
            return self._handle_interrupt()
        message = get_finalized_message(tool_output, tool)
        self.append_and_pipe(message)
        return tool, tool_output

    def _handle_interrupt(self) -> tuple[None, Empty]:
        logger.info("Handling interrupt for agent '%s'", self.name)
        self.pipe.clear_interrupt()
        self.append_and_pipe(
            get_finalized_message(
                Str(value=INTERRUPTED_GENERATION_CONTEXT),
                message_kind=MessageKind.INTERRUPTED_GENERATION,
            )
        )
        interrupt_input = self.prompt_user_tool.InputModel(
            value=INTERRUPT_PROMPT_TO_USER
        )
        interrupt_output = self.prompt_user_tool(interrupt_input, self.messages)
        message = get_finalized_message(interrupt_output, self.prompt_user_tool)
        self.append_and_pipe(message)
        self._ephemeral_default_tools = []
        return None, Empty()

    def _handle_provider_request_error(
        self, error: LLMProviderRequestError
    ) -> tuple[None, Empty]:
        logger.info(
            "Recoverable provider request failure in agent '%s': %s",
            self.name,
            type(error).__name__,
        )
        retry_input = self.prompt_user_tool.InputModel(
            value=LLM_PROVIDER_REQUEST_RETRY_PROMPT
        )
        retry_output = self.prompt_user_tool(retry_input, self.messages)
        self.append_and_pipe(get_finalized_message(retry_output, self.prompt_user_tool))
        self._ephemeral_default_tools = []
        return None, Empty()

    def invoke(
        self,
        *,
        input: Empty | None = None,
        dry_run: bool = False,
    ) -> tuple[Stop, list[Message]]:
        """Run the agent synchronously until it stops or is cancelled."""
        stack_token = None
        output_token = None
        exit_status = RunStatus.FAILED
        logger.debug(
            "Invoke start: agent='%s' stack=%s dry_run=%s",
            self.name,
            get_active_agent_stack(),
            dry_run,
        )
        try:
            if self.interaction_mode is not None:
                output_token = bind_output(self.interaction_mode)
            self._initialize_pipe_for_invoke(dry_run=dry_run)
            if not dry_run:
                stack_token = push_active_agent(self.name)
            self._init_messages()
            self._loaded_skills.clear()
            self.auto_load_skills()
            self._ephemeral_default_tools = []

            tool = None
            output = Empty() if input is None else input
            while True:
                tool, output = self._advance_by_step(tool, output)
                if isinstance(output, Stop):
                    exit_status = RunStatus.COMPLETED
                    return output, self.messages
        except (StopAgent, KeyboardInterrupt, ExternalCallCancelledError) as e:
            exit_status = RunStatus.CANCELLED
            logger.info(
                "Invoke terminating: agent='%s' via %s",
                self.name,
                type(e).__name__,
            )
            if isinstance(e, KeyboardInterrupt):
                raise
            return Stop(), self.messages
        finally:
            logger.debug(
                "Invoke end: agent='%s' status=%s",
                self.name,
                exit_status,
            )
            self.pipe.finalize_run(status=exit_status)
            if stack_token is not None:
                pop_active_agent(stack_token)
            if output_token is not None:
                reset_output(output_token)

    def show_agent_info(self):
        """Render configured tools, storage locations, and initial messages."""
        self.pipe.initialize(dry_run=True)

        console = Console()
        self._init_messages()
        self.auto_load_skills()
        self._show_tools(console)
        print()
        self._show_locations(console)
        print()
        self._show_initial_messages(console)

    def _show_tools(self, console: Console):
        conf_table = Table(title="Tools", show_header=True, header_style="bold magenta")
        conf_table.add_column("Type", style="dim")
        conf_table.add_column("Name", style="cyan")
        conf_table.add_column("Description")
        conf_table.add_column("Chained To")

        no_description = "Not provided"

        def _get_chained_to(t: Tool) -> str:
            if not t.chained_to:
                return "-"
            return ", ".join([c.name for c in t.chained_to])

        conf_table.add_row(
            "Main Prompter",
            self.master_tool.name if self.master_tool.name else "Not set",
            "Set the flag 'is_agentic' to true for agentic flows.",
            "-",
        )

        for id, t in self.active_tools.items():
            if id != self.master_tool.id:
                conf_table.add_row(
                    "Active",
                    t.name,
                    t.description or no_description,
                    _get_chained_to(t),
                )

        for id, t in self.passive_tools.items():
            conf_table.add_row(
                "Passive",
                t.name,
                t.description or no_description,
                _get_chained_to(t),
            )

        default_tool_names = (
            ", ".join(t.name for t in self.default_tools)
            if self.default_tools
            else "None"
        )
        conf_table.add_row(
            "Default Tools",
            default_tool_names,
            "Configured fallback tool list.",
            "-",
        )

        console.print(conf_table, end="\n\n")

    def _show_locations(self, console: Console):
        loc_table = Table(
            title="Persisted Locations", show_header=True, header_style="bold green"
        )
        loc_table.add_column("Item", style="dim")
        loc_table.add_column("Path", style="blue")
        loc_table.add_column("Items", justify="right", style="dim")

        data_root = self.pipe.data_path if self.pipe.data_path else None
        if data_root is None:
            loc_table.add_row("Data location", "Not set", "-")
            console.print(loc_table, end="\n\n")
            return

        n = (
            sum(1 for _ in data_root.iterdir())
            if data_root.is_dir() and data_root.exists()
            else 0
        )
        loc_table.add_row("Data location", str(data_root), str(n))
        console.print(loc_table, end="\n\n")

    def _show_initial_messages(self, console: Console):
        msg_table = Table(
            title="Initial Messages", show_header=True, header_style="bold yellow"
        )
        msg_table.add_column("Type", style="dim")
        msg_table.add_column("Content")
        if Role.SYSTEM not in {m.role for m in self.messages}:
            self.messages = [Message(role=Role.SYSTEM, content="None")] + self.messages

        for m in self.messages:
            if m.role == Role.USER:
                msg_table.add_row("User", m.content)
                msg_table.add_row("", "")
                continue
            msg_table.add_row("System", m.content)
            msg_table.add_row("", "")

        console.print(msg_table)
        self.messages.clear()
