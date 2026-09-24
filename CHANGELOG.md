# Changelog

## Unreleased

- Add the opt-in Shed `SafeScripts` capability. Its single `run_shell_script`
  tool discovers protected `.sh` entrypoints and runs them with bounded output,
  deadlines, cancellation, and script output events.

## 0.1.2a2 - 2026-09-23

- Breaking: rename the RoboSprawl Shed deployment recipe and orientation skill
  to Robozium. Import `robozium` from `roboz.shed.deployments.robozium` and
  `roboz.shed.skills.robozium`; the old `robosprawl` names are removed.

## 0.1.2a1 - 2026-09-23

- Add password-encrypted `.env.encrypt` output beside an untouched `.env`,
  with a hidden-password CLI command, explicit loading, and deferred endpoint
  loading. Plaintext dotenv files remain supported.

- Breaking: replace the endpoint inventory `export`, `import`, and `reset`
  commands with `init` and `generate`. Project JSON remains the editable source;
  generated Python refreshes without `--force` while unrelated files remain
  protected. Bundled and generated catalogues retain typed model autocomplete.

## 0.1.2.dev12 - 2026-09-21

- Breaking: consolidate core, Shed, and Endpoints into the `roboz`
  distribution. Replace `roboshed` imports with `roboz.shed` and
  `roboz_endpoints` imports with `roboz.endpoints`; no compatibility packages
  or forwarding namespaces are provided.
- Install the OpenAI SDK as a required dependency while retaining deferred
  endpoint client construction and credential-free imports. Remove package
  extras and the `roboz-endpoints` executable; manage catalogues with
  `python -m roboz.endpoints inventory`.
- Exclude Proton Bridge from the consolidated distribution. Its pre-migration
  implementation and development setup remain preserved on the
  `wip/proton-bridge` branch.
- Consolidate package documentation into the main README and move the detailed
  project catalogue guide to `docs/catalogues.md`.

## 0.1.2.dev11 - 2026-09-21

- Ship the simple and complex examples in `roboz.examples`, so installed users
  can run the credential-free agent demo with `python -m roboz.examples.simple`
  without cloning the repository.

## 0.1.2.dev10 - 2026-09-21

- Define default tools as scheduler-owned chain roots: downstream tools can now
  reference a default-only parent with `chained_to`, while defaults that are
  themselves chained downstream are rejected during agent construction.

## 0.1.2.dev9 - 2026-09-20

- Remove the leftover `proton-bridge-beta` installation extra. Install
  `roboz-proton-bridge` directly, consistently with the other independently
  published companion distributions.

- Make images and links in the published package description resolve outside
  the GitHub repository.

## 0.1.2.dev8 - 2026-09-20

- Breaking: remove the legacy agent master tool. Model-driven agents run
  their configured default tools followed by the built-in `prompt_agent` tool;
  deterministic agents require and run only their configured default tools.
  Use `default_tools` to configure deterministic steps instead of relying on
  the removed `master_tool` attribute.

- Show every displayed tool's external dependency IDs and kinds in
  `Agent.show_agent_info()`.

## 0.1.2.dev7 - 2026-09-20

- Breaking: replace the `is_agentic` boolean with the string-valued
  `AgentMode.DETERMINISTIC`, `.STEERABLE`, or `.AUTONOMOUS` enum (the default
  remains steerable). Autonomous agents use model-driven scheduling without a
  user-input tool or direct user-interaction sidecar; their tools communicate
  through the event pipe. Remove `interaction_mode` arguments and
  `set_interaction_mode` calls: invocation uses a bound host channel, defaulting
  to CLI, and restores the previous binding automatically. Registering a
  `UserIO` adapter selects `Output.API` for the binding's lifetime.
  `AgentMode` now lives in `roboz.models`; the existing `roboz.agent` export
  remains available for compatibility.

- Run each configured default tool exactly once per cycle for deterministic agents.
  Model-driven agents retain their master-tool step.

- Filter conversation history with `roboz.models.filter_messages` by role,
  requested action, or result caller while retaining message order and metadata.

- Call `get_completion(endpoint=..., messages=...)` directly without an API
  callback. `MockLLMEndpoint` now accepts literal text responses as well as JSON
  objects and exceptions.
- Breaking: completions without an output model or tool list now return raw text
  verbatim instead of attempting action validation. Supply `LlmOutputModel=...`
  or `active_tools=...` to retain structured dictionary results and repair retries.
  Endpoint and callback arguments are mutually exclusive. See the
  [completion guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/tool-authoring.md#standalone-llm-backed-tools).

## 0.1.2.dev6 - 2026-09-15

- Breaking: make the `roboz` root a lazy authoring facade. It now exports only
  `Agent`, `Skill`, `Tool`, `Factory`, `tool`, and `factory` alongside discoverable
  domain namespaces. Import models from `roboz.models`, agent helpers from
  `roboz.agent`, built-in tools from `roboz.tools`, and context protocols from
  `roboz.tooling`. See [the import migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/imports.md).

## 0.1.2.dev5 - 2026-09-13

- Add `LLMEndpointRoute`, a typed live-selection layer above concrete chat
  endpoints. Routes retain a caller-owned endpoint getter, report the current
  selected resource without initializing clients, and resolve once per model
  operation so in-flight calls retain their endpoint. Request and OpenRouter
  policies support routes while keeping fixed endpoint use unchanged.

- Inspect a configured deployment with `DeployableAgent.external_dependencies()`.
  It builds fresh, unstarted agents without event sinks and delegates to their
  existing tool inspection, including child agents and skill resources. Capability
  construction still runs normally; no additional dependency declaration is required.

- Initialize factory contexts lazily on invocation through the optional
  `Materializable.materialize() -> Self` protocol. Binding, copying, and inspection
  stay side-effect free. Endpoints support explicit early `materialize()` calls
  while preserving their identity; prompt contexts delegate initialization.
  Breaking: OpenAI-compatible client protocols now require `close() -> None` for
  typed cleanup. See [the lifecycle contract](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md).

- Make resource-inspection declarations explicit: aggregate contexts and agents
  implement the `HasExternalDependencies` protocol, renamed from `Context` with
  no compatibility alias. Authors can inherit it to require
  `external_dependencies()`; missing implementations fail type checking and
  instantiation. Plain contexts remain unrestricted, and direct resources inherit
  inspection from `ExternalDependency`.

- Breaking: migrate core interaction and agent factories to concrete contexts.
  Bind interaction factories to strings, `run_subagent` to the child `Agent`,
  and background/prompt factories to `BackgroundAgentContext`/`PromptAgentContext`
  from `roboz.agent`. Background constructors own fresh state; rebinding and copies
  share supplied state. Agent inspection uses live tool methods, and deployment
  builders use the same bindings. Restore top-level agent, skill, and built-in
  tool exports; removed dependency and `Ctx` APIs stay removed. See
  [the primitive migration](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md).

- Breaking: core endpoints require synchronous OpenAI-compatible clients instead
  of an untyped client. Client methods and request controls are checked statically;
  the real `openai.OpenAI` client satisfies the protocols without a wrapper or an
  SDK dependency in core. Replace placeholder or incompatible clients with a
  conforming chat/transcription client. See [the client contract](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md).

- Breaking: external resource implementations must provide `check() -> bool` for
  explicit availability checks. Executables check PATH; core endpoints use model
  discovery without generating output. Results are uncached, absent resources
  return `False`, and check errors propagate. Binding, copying, and inspection
  never invoke checks. Plain contexts need no check method. See
  [resource inspection and availability](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md).

- Accept any concretely typed factory context, including plain objects and lists.
  Contexts without resource inspection are passed through unchanged and their
  tools report no dependencies. Wrong context types remain static binding errors.

- Bind core chat and transcription endpoints directly as typed factory contexts,
  retaining their identity and client for execution and inspection. Endpoint
  request helpers and the selector now use concrete endpoints; lazy construction
  and companion migrations remain deferred. Scripted endpoints report no external
  resources. See [typed contexts and resource inspection](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md).
- Fix `@factory()` input inference so parenthesized factories retain the same
  concrete input, output, and context types as bare `@factory` declarations.

- Breaking checkpoint: bind factories directly to concrete typed resources or
  contexts with optional `external_dependencies()`, preserving the supplied object and
  inspecting it live. Replace `Ctx` and tool dependency properties with concrete
  contexts and `tool.external_dependencies()`. Dependency extension primitives
  stay in `roboz.dependencies`; the top-level API now exposes only data and
  tool/factory/context primitives. Remove legacy dependency bases, lazy/reference
  helpers, and checker registration. Agent, built-in tool, and companion consumers
  remain unmigrated; this checkpoint is not release-ready. See [typed contexts and resource inspection](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/dependency-primitives.md)
  for the contract, removed APIs, and migration boundary.

## 0.1.2.dev4 - 2026-09-12

- Breaking: make `DeployableAgent` an explicitly configured class. Constructor
  capabilities are protected defaults; append capabilities and child agents with
  the add methods, configure endpoints and runtime values separately, and unpack
  `(agent, background_agents)` from `build()`. `build_graph()` is removed.
- Breaking: capability builders now declare concrete `required_attributes` and
  receive `build(agent, pipe)`. Builds validate the complete graph before
  constructing sinks, pipes, or tools and aggregate missing, `None`, and
  incorrectly typed owner attributes.

## 0.1.2.dev3 - 2026-09-11

- Breaking: remove the redundant `roboz[shed]` installation extra. Install
  `roboshed` directly; it installs its compatible core Roboz dependency.

- Add `DeployableAgent.build_graph()` to return the root agent and all background
  handles for host lifecycle control. `build()` still returns the root alone.

- Breaking: replace `AgentDefinition` and `SubAgentSpec` with recursive
  `DeployableAgent` definitions. Put child definitions directly in `subagents`
  or `background_agents`; delegation tools use the child's name and description,
  and background agents start through default tools. Construct generic
  definitions directly with `DeployableAgent(...)`.
  Sandbox-aware `Deployment` lives in `roboshed.deployments`, not core.
  See [the archived agent migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/agent-factories.md) for migration.

## 0.1.2.dev2 - 2026-09-09

- Breaking: remove the `roboz[openai]` extra. Install
  `roboz-endpoints[openai]` directly for the endpoint catalogue and SDK adapter;
  it installs core automatically.

- Breaking: move dependency primitives from `roboz.tooling.dependencies` and
  `roboz.tooling` to `roboz.dependencies`. Update those imports; the existing
  top-level `roboz` authoring API remains available. See [the archived context migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/context-migration.md).

- Add `DependencyRoute` for live caller-owned selections. Materialization and discovery delegate to the current target without introducing a separate identity or caching the selection.

- Breaking: capability builders receive `build(pipe, *, default_endpoint)`.
  Capabilities own their tool-specific endpoint choices and receive the agent
  endpoint as a fallback, independently of runtime controls. Lazy/live
  references retain identity and deferred resolution. See
  [the archived agent migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/agent-factories.md) for builder migration.

- Breaking: move the Librarian and its memory/summarization tools out of core into `roboshed.agents` and `roboshed.tools`. Use `librarian(sandbox, agent_names, agent_endpoint=...)` instead of `LibrarianConstructor` and its path record; see [the archived agent migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/agent-factories.md). Core retains control, interaction, and generic construction primitives.

- Add generic `AgentDefinition`, `AgentCapability`, `Capability`, and `SubAgentSpec` in `roboz.deployment`. Configure all tools and skills through capabilities, with fresh agent pipes and explicit sink configuration; definitions select no project, memory, or persistence conventions. See [the archived agent migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/agent-factories.md).

- Breaking: `roboz[shed]` now installs `roboshed` instead of `roboz-shed`. Update direct requirements to `roboshed` and imports from `roboz_shed` to `roboshed`; no compatibility package is provided.

### Added

- Select lazy model endpoints with `ModelSelector` from `roboz.llm` or
  `roboz.llm.endpoints`, without requiring Shed or constructing clients.

- Bind exact dependency registrations with `roboz.dependencies`, independently
  of Shed. Registration types, checker callbacks, and contract errors now live
  alongside dependency discovery and resolution primitives.

- Add `ExternalDependencyReference` for replaceable resources. Endpoint helpers
  preserve live selection and discovery through contexts, tools, and agents,
  while lazy resources retain their identity validation and cached clients.

- Inspect resources before binding tools with `Ctx.external_dependencies()`.
  Contexts implement `ExternalDependencySource`, preserving resource identity
  and reflecting live nested contexts, agents, and catalogs without resolving
  lazy resources. `external_dependencies` is now a reserved context field name;
  see [the archived context migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/context-migration.md).

### Fixed

- Refresh lifecycle endpoint metadata when invoking an agent again after a live
  reference switches models, while retaining each lazy dependency's client cache.

### Changed

- **Breaking:** Construct tool contexts with `Ctx(**values)` and pass resources
  directly. Remove `FactoryCtx`, specialized context classes, `ToolDependency`,
  and endpoint binding wrappers; `Tool.dependencies` now returns resources.
  See [the archived context migration guide](https://github.com/Tachion-Oy/roboz/blob/roboz-v0.1.2.dev7/docs/context-migration.md) for replacements and state ownership.

- Define and enforce Google-style source docstrings, and normalize tool
  docstrings before including them in agent prompts.

## 0.1.1

- Preserve concrete nested model types across in-memory tool handoffs while
  keeping field projection, excluded fields, validation, and detached inputs.
- Add optional companion extras and independent package release workflows.
- Keep the core's runtime dependency set unchanged.
