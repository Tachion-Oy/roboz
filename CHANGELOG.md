# Changelog

## Unreleased

## 0.7.0a1 - 2026-10-09

- Replace the read-only `DeployableAgent` properties `name`, `description`,
  `system_prompt`, `mode`, `automatic_tool_prompt`, `agent_endpoint`, and
  `initial_messages` with writable attributes. Existing reads and configuration
  methods remain supported; `set_attributes()` still protects structural fields.
- Breaking: external dependency `check()` now returns `None` on success or
  `DependencyFailure(reason_code, message)`. Each resource implements public
  `check()` and supplies its diagnosis; callers use `check() is None`. Preserve
  redacted error causes in health records and log failure changes centrally,
  including script helper connection and protocol failures.

## 0.6.1a1 - 2026-10-06

- Add `DeployableAgent.resolve_capabilities()` for effective launch choices and
  expose `kind` on capability labels for direct metadata responses.
- Return the agent definition from the existing Robozium recipe and include its
  fixed guidance/compactification plus selectable SafeScripts and email. Attach
  additions and selections to that definition before building runtime agents.
  Proton Bridge accepts deferred settings for credentials unlocked after startup.
  `ProtonBridgeSettings.from_env(prefix=...)` owns environment parsing and
  validation, with redacted errors for missing or invalid settings.

## 0.6.0a1 - 2026-10-06

- Breaking: unify deployable-agent capabilities under `capabilities=` and
  `add_capabilities()`, with typed `CapabilityLabel`, `ToolLabel`, and `SkillLabel`
  metadata. Register existing objects with keyword-only
  `Capability(label=..., value=...)`. Builders return runtime tools, chains, or
  skills in a tuple; deployment applies the declared label to each value.
  `ToolLabel(default=True)` preserves ordered default-tool
  scheduling. Each definition owns its selection through
  `set_capability_selection()`, including skill loading modes.
  Custom builders now inherit from the ordinary `Capability` class instead of
  implementing `AgentCapability`. Shed capabilities also use ordinary classes
  with keyword-only constructors, such as `Email(service=...)`.
  `Email` preserves its declared skill label and binds its guidance and tools
  together, enabling either automatic or on-demand loading.
  Fixed labels stay included, disabled builders are skipped, and dependency
  inspection covers all declarations without registering conflicting alternatives
  together in a runtime. `Filesystem` loading is now
  expressed by its skill label instead of `auto_load_skill`. No compatibility
  aliases or mandatory primitive tool set are introduced.

## 0.5.0 - 2026-10-05

- Add `Skill.factory(...)`, a typed classmethod returning a context-taking
  factory. Calling the factory builds a complete skill with concrete tools;
  agent and deployment input contracts are unchanged.
- Breaking: consolidate the CLI and file-editing skills into
  `filesystem_skill(FilesystemContext(permissions=..., pipe=...))`, containing
  both guarded command and patch chains. Replace `FileCommands()` and
  `FileEditing()` with one `Filesystem()` capability. Direct callers register
  the constructed skill instead of separate instructions and tool chains.
  `Filesystem(auto_load_skill=False)` registers the complete skill for on-demand
  loading instead of exposing tools without their instructions.
  The skill action changes from `cli_tools` / `file_editing` to `filesystem`;
  `run_file_command`, `apply_patch`, and standalone tool builders remain available.
- Breaking: rename `prompt_agent` to `prompt_llm` and `PromptAgentContext` to
  `PromptLLMContext`; import them from `roboz.agent` or
  `roboz.agent.prompt_llm_tool`. Rename `run_subagent` to `run_nested_agent`,
  available from `roboz.agent` or `roboz.agent.nested_agent`.
  Use `nested_agents` and `add_nested_agents()` instead of `subagents` and
  `add_subagents()` on deployment definitions; the Shed orchestrator also takes
  `nested_agents`. Tool identifiers are now `PROMPT_LLM_TOOL_NAME` and
  `RUN_NESTED_AGENT_TOOL_NAME`. The old names and modules are removed without
  compatibility aliases. Execution behavior is unchanged: agents own the tool
  loop, LLM prompting chooses its next action, and nested agent calls wait for
  their result. Update caller/action filters for the new tool identifiers;
  existing persisted records are not rewritten.

## 0.5.0rc1 - 2026-10-04

- Preserve filesystem approval questions, exact user replies, and decisions in
  guarded tool reports, including declines and subsequent execution failures.
  Reserve a separate CLI report budget for permission messages so large command
  output does not discard them.
  Clarify Robozium's hub layout, cross-project reads, shared-write approvals,
  and link restrictions in the agent skills.
- Prepare the first 0.5 release candidate with Beta package metadata.
- Consolidate the Shed file CLI, endpoint catalogue, and secret-loading guides
  into the root README, replacing the separate package READMEs.

## 0.5.0a1 - 2026-10-04

- Breaking: replace the file CLI with one command-token interface under
  `roboz.shed.tools.cli_commands`. Use `get_run_file_command`, `FileCommand`,
  and `Token`; the tool action is `run_file_command` with ordered `[value, tag]`
  pairs in `value`. Remove the previous input models, command-spec customization,
  runtime help, versioned package, and transfer compatibility imports. Update
  Python callers and stored tool calls before replay; no automatic conversion
  or compatibility aliases are provided.
- `FileCommands` now exposes the full guarded command set: discovery, reading,
  searching, comparison, writing, directory creation, copying, moving, trash,
  and removal. Existing sandbox policies still govern each required operation.
- Support mixed `&&`, `||`, `;`, and `|` with zsh-style precedence, guarded
  fallbacks, fresh path expansion per reached command, and exact stdout bytes
  through buffered pipelines. Preserve native output, exit status, and partial
  effects; command output is bounded and reports mark omitted earlier history.
- Guard recursive operations before execution, including overwrite permissions,
  hidden entries, and search ignore files. Support source `*` and one recursive
  `**/` component with default zsh semantics; unmatched patterns fail. Discovery
  checks READ on starting roots; deletion can remove terminal symlinks without
  following them. Search preflight and deletion preparation have 60-second deadlines.
- Use one permission guard and chain builder for CLI commands, patches, and
  email attachments. All tools check every requirement before any approvals and
  preserve literal spaces and backslashes in permission patterns. Patches and
  attachment downloads now apply ask rules to READ/DELETE overwrite requirements
  and reject symlinks and hard-linked write targets using the CLI path helpers.
- Move detailed CLI usage into the CLI skill, with executable examples covering
  mixed chains, pipelines, fallbacks, skipped commands, and inline file content.
  `FileCommands` registers the tools and skill together; direct factory callers
  must register `roboz.shed.skills.cli_skill` in `auto_loaded_skills` or `skills`.
- Support OpenAI SDK 3.x alongside 2.x and update the locked SDK to 3.22.1.
- Raise dependency minimums and update locked python-dotenv, Pygments, pytest,
  and urllib3 versions to address known vulnerabilities. Add weekly dependency
  updates and vulnerability auditing in CI.

## 0.4.0a1 - 2026-10-02

- Breaking: Shed `run_file_command` now requires shell-style `chain` values:
  `"|"`, `"&&"`, `"||"`, or `";"`. Replace `"pipe"` with `"|"`; replace legacy
  unconditional `"and"` with `";"`, use `"&&"` for dependent success-only steps,
  and `"||"` for fallback steps. Results now report the last executed status
  and preserve stderr separately. Update Python callers and stored calls before
  replaying them.

## 0.3.1a1 - 2026-09-28

- Add a Linux host service command for SafeScripts, with `serve` and
  greeting-only `check` subcommands using the existing socket transport.

## 0.3.0a1 - 2026-09-28

- Breaking: Email, FileCommands, and FileEditing capabilities now require
  `set_attributes(sandbox=...)` with a configured project scope instead of
  `permissions=...`. The orchestrator binds only this sandbox.

- Extend `SafeScripts` with Linux execution through a private Unix socket and
  `serve_scripts`, preserving the tool schema and output events. Host settings
  control remote execution; health checks use a bounded handshake.
- Stream SafeScripts catalogues entry by entry and retain partial output with
  terminal diagnostics. The private socket protocol is now version 2; upgrade
  clients and helpers together (package versions need not match).
- Breaking: local `SafeScripts` requires the agent's `Sandbox` for its working directory
  instead of `PermissionPolicy`. Scripts do not enforce file-tool permission rules.

## 0.2.1a2 - 2026-09-27

- Export `SECRET_SUFFIX`, `ENCRYPTED_NAMESPACE`, and
  `DEFAULT_ENCRYPTED_ENV_PATH` from `roboz.endpoints` and `roboz.endpoints.env`
  for credential integrations, preserving encryption and loading behavior.

## 0.2.1a1 - 2026-09-26

- Add README navigation, a dedicated API key encryption section, and
  Robozium links and screenshots.

## 0.2.0a1 - 2026-09-25

- Add an `Email` capability for configured email services, including Proton
  Bridge. Compose it with `SafeScripts` through Robozium's additional capabilities.
- Add the opt-in Shed `SafeScripts` capability. Its single `run_shell_script`
  tool discovers protected `.sh` entrypoints and runs them with bounded output,
  deadlines, cancellation, and script output events.
- Breaking: Proton Bridge now uses IMAPClient and accepts `client_factory`
  instead of `imap_factory`. Simplify the email implementation and fix draft
  deduplication, cancellation before writes, and connection cleanup.
- Add an explicitly configured Proton Mail Bridge provider to Shed's guarded
  email tools, supporting search, reads, attachments, and unsigned or signed
  draft creation with verified TLS.
- Breaking: dotenv encryption now selects only names ending in `_SECRET`, and
  `load_api_keys` is replaced by `load_secrets`. Rename API credentials such as
  `OPENAI_API_KEY` to `OPENAI_API_KEY_SECRET` and recreate `.env.encrypt`.
- Proton Bridge's automated tests cover fake IMAP and scripted TLS; live Bridge
  validation remains outstanding.

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
