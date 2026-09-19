# Changelog

## Unreleased

## 0.1.1.dev4 - 2026-09-20

- Migrate the Librarian and capability examples to Roboz's explicit
  `mode=AgentMode.DETERMINISTIC` configuration. Breaking: remove
  `interaction_mode` arguments from `orchestrator` and `robosprawl` calls;
  invocation uses a bound host channel, defaulting to CLI. Registering a
  `UserIO` adapter selects `Output.API` for the binding's lifetime.

- Keep the Librarian active for a final maintenance sweep after watched runs become
  idle, so terminal conversation facts reach snapshots and memory before it stops.
  Snapshot coverage now relies only on persisted message sequences, and maintenance
  artifacts are published atomically.

## 0.1.1.dev3 - 2026-09-13

- Migrate capability bindings to the central typed contexts. Existing capability
  arguments, owner configuration, endpoint overrides, defaults, and maintenance
  order are preserved. Each build creates fresh tool state and reports actual
  resources without initializing model clients. The RoboSprawl recipe now uses
  `LLMEndpointRoute` for the selectable orchestrator while its Librarian endpoint
  remains fixed.

- Breaking: email factories now use the central `EmailContext`; attachment
  resolvers bind a `Path` directly. `get_work_with_email` keeps its arguments and
  requires a complete `EmailService`, which now inherits `ExternalDependency`
  and implements `check()` using its read-only probe. Email dependencies are
  inspectable without mailbox access and can be monitored without an agent.
  Mailbox operations, permission checks, defaults, and error messages are preserved.
  See the [context migration guide](../../docs/shed-tool-contexts.md#email-services-and-contexts).

- Breaking: bind built-in file and maintenance factories to concrete typed contexts
  instead of `Ctx`; direct patch stages accept a `Path` or `TruncationSpec`.
  Existing `get_*` helper arguments remain supported. Command and summary contexts
  report their actual resources through `tool.external_dependencies()`. Compaction
  counters belong to the context: rebinding/copying shares them; constructing a new
  context creates fresh state. See the [tool-context migration](../../docs/shed-tool-contexts.md).

- Breaking: remove callback-based `inspect_dependencies`; inspect the configured
  `DeployableAgent.external_dependencies()` instead; it constructs unstarted
  agents using normal capability builders, without temporary sandbox isolation.
  The health monitor accepts the resulting resources together with standalone
  dependencies such as selectable models, deduplicating the combined sequence. Each resource owns its
  synchronous `check() -> bool`; remove checker registrations and replace the
  three category-specific probe helpers with `check_dependency(resource)` when
  a sanitized observation is needed. Timeout, concurrency, metadata filtering,
  and cached health-record behavior remain unchanged. See the README health guide.

- Breaking: replace the setter-based `RoboSprawl` class with `robosprawl(sandbox, ...)`.
  Supply deployment choices directly and unpack the returned root/background
  agents. See [the recipe guide](../../docs/agent-factories.md).

## 0.1.1.dev2 - 2026-09-12

- Breaking: move permission, sandbox, watched-agent, and default model inputs
  from Shed capability objects to their owning `DeployableAgent` configuration.
  Capability-specific thresholds, limits, timeouts, and endpoint overrides stay
  on the capability objects.
- Breaking: remove `Deployment` and replace the callable `RoboSprawl` dataclass
  with a standalone configuration class. Construct `RoboSprawl()` without
  inputs, supply runtime inputs through its setters, then call argument-free
  `build()` to obtain fresh root/background agents. `robosprawl` aliases the
  class, not the former function signature. The existing orchestrator defaults,
  Librarian pipeline, project context, and persistence remain in the recipe.
  Use one configuration instance per new run; see `docs/agent-factories.md`.

## 0.1.1.dev1 - 2026-09-12

- Add `roboshed.deployments.robosprawl.RoboSprawl`, a callable configuration
  object for a persistent orchestrator with Librarian-backed memory. Configure
  the memory endpoint, additional capabilities, specialists, and interaction
  mode; supply the scoped sandbox, project slug, model getter, and event sinks
  for each run. It returns an unbuilt `Deployment` without starting agents or
  creating directories.

## 0.1.0a4 - 2026-09-11

- Reject symbolic-link project folders so an alias cannot inherit another
  project's paths and write permissions.
- Add `Sandbox.for_project(project_slug)` to create a validated, separately
  scoped sandbox without changing the configured instance or creating directories.

- Breaking: replace RoboSprawl's configuration/factory layers with a configured
  `Deployment(agent=..., sandbox=...)` from `roboshed.deployments`. Set scope
  through `deployment.sandbox.configure_scope(folder)`, configure the exposed
  event sinks, and unpack `agent, background_agents = deployment.build()`.
  Build creates fresh runtimes without starting them.
- Restore the `orchestrator` and `librarian` constructors in `roboshed.agents`.
  Pass the sandbox to both and recursive foreground names to the Librarian. The
  orchestrator owns stop and scoped file work, while the Librarian owns its
  ordered maintenance pipeline and uses its agent endpoint as the maintenance
  model default. `Deployment.additional_capabilities` appends root-only
  application features without reconstructing those defaults.
- Remove `AgenticFactory`, `DeploymentFactory`, `DeploymentRecipe`, `RunFactory`,
  and `RoboSprawlBundle`. Dependency inspection lives in
  `roboshed.dependency_health`, using an isolated temporary-sandbox callback.
- Breaking: replace public Shed `Workspace` and `Project` values with
  `Sandbox`. Construct it with the application's actual root, configure its one
  scope, and then construct agents. Scope selection does not create directories.
- Remove the concrete `roboshed.deployments.robosprawl` preset. RoboSprawl owns
  its paths, endpoints, extra capabilities, agent graph, and `Deployment`
  instance in its application configuration.

See `docs/agent-factories.md` for migration.

## 0.1.0a3 - 2026-09-10

- Breaking: replace the public Shed `Workspace` and `Project` primitives with a
  single `Sandbox`. Configure tier and persistence paths once, derive the policy
  with `sandbox.permissions(project_slug)`, and pass the sandbox plus slug to
  RoboSprawl deployment factories. Import `Sandbox` and the standalone
  `PermissionPolicy` from `roboshed.sandbox`. The tiered permission behavior is
  unchanged.

## 0.1.0a2 - 2026-09-09

- Treat wildcard characters in project and workspace names literally when
  deriving permissions, preventing writes from extending into other directories.
- Keep dependency health scheduling alive after observation failures and allow
  a stopped scheduler task to restart, with sanitized diagnostics. Infer checker
  registrations from the first dependency for each ID, matching core binding.

- Allow `RoboSprawl.librarian_capabilities` to configure maintenance per project
  and recursive foreground names. The default remains snapshots, consolidation,
  retention, and a 120-second cadence, in that order.

- Add the `robosprawl` orientation skill following PeffaHub’s hub, Librarian, and HUD guidance. Resolve actual paths separately through `RoboSprawl.project_context`; obsolete safe-script behavior is omitted.

- Configure `RoboSprawl` with a capability sequence and project instruction template; permission factories bind during each recipe invocation, so consumers need no configuration callbacks.

- Move `ModelSelector` from `roboshed.deployments.models` to
  `roboz.llm.endpoints`, also exported by `roboz.llm`. Update imports; selection
  behavior is unchanged. See `docs/context-migration.md`.

- Move registration and binding from `roboshed.dependencies.contract` to
  `roboz.dependencies`, and health checks from `roboshed.dependencies.health` to
  `roboshed.dependency_health`. Update imports to these paths; the former
  dependency package is removed. See `docs/context-migration.md`.

- Add the `RoboSprawl` deployment recipe: consumers select project capabilities and models while shared composition derives project instructions, recursive agent names, and Librarian maintenance. Host-specific instructions and interaction channels remain explicit inputs.

- Derive standard workspace boundaries with `Project.permissions`: workspace reads, project writes, confirmation for shared writes, and denial elsewhere. No application permission factory is needed.

- Add recipe-driven `DeploymentFactory`, reusable `RunFactory` and model selection, isolated dependency inspection, and exact registrations with safe health monitoring. Recipes explicitly own dependency reuse; construction starts no agents or background tasks.

- Breaking: move email input models into `roboshed.tools.email.inputs`; they
  remain exported from `roboshed.tools.email`. Remove `roboshed.assistant`,
  `roboshed.demo`, and the `roboz-demo` command. Compose file agents directly
  from `AgentDefinition` and Shed capabilities; see `docs/agent-factories.md`.

- Breaking: compose the Librarian through its `capabilities` argument, like the
  orchestrator. Public `ConversationSnapshots`, `MemoryConsolidation`,
  `ArtifactRetention`, and `MaintenanceCadence` live in `roboshed.capabilities`.
  Move project inputs and `LibrarianTuning` settings to the relevant capability;
  the aggregate tuning class is removed. See `docs/agent-factories.md`.

- Breaking: capabilities pass configured endpoints directly to their tools,
  falling back to the agent's model when unset. Librarian snapshotting and
  consolidation can use separate models. Use
  `librarian(sandbox, agent_names, agent_endpoint=...)` for a shared default,
  with `endpoint` overrides on `ConversationSnapshots`
  and `MemoryConsolidation`. Its special `endpoint_factory` argument
  is removed; endpoints and runtime cancellation remain independent inputs.
  See `docs/agent-factories.md`.

- Add `roboshed.deployments.robosprawl.AgenticFactory` to bind project persistence, initial memory, and background-start capabilities to configured definitions. Builds return `RoboSprawlBundle(agent, background_agents)`, a named tuple for direct invocation and host cancellation; construction starts no threads. See `docs/agent-factories.md`.

- Breaking: own orchestrator/Librarian presets, workspace/project structure, reusable capabilities, and memory/summarization tools. Import presets from `roboshed.agents`, feature implementations from `roboshed.capabilities`, and explicit paths/permission inputs from `roboshed.workspace`. Both presets return `AgentDefinition`; the orchestrator remains available across tasks until asked to stop. Preset extensions use only `capabilities`; move separate tool/skill inputs into capability builds. See `docs/agent-factories.md`.

- Breaking: rename the distribution from `roboz-shed` to `roboshed` and the import namespace from `roboz_shed` to `roboshed`. Update dependency declarations and imports. No compatibility package is provided.

### Added

- Compact active conversations with configurable continuation prompts, context thresholds, cancellation, and optional per-attempt timeouts.

### Changed

- **Breaking:** Replace specialized tool contexts with `roboz.Ctx(**values)`
  and use service/executable resources directly. Existing builders retain their
  defaults and guard behavior. See `docs/context-migration.md` in the repository
  for the low-level API migration.

- Document Shed's agent-facing tools under the shared docstring contract.

### Fixed

- Reject relative project persistence paths that escape through traversal or
  symlinks. External logs, snapshots, and memory require explicit absolute paths;
  see `docs/agent-factories.md` for migration guidance.
- Skip conversation files with invalid UTF-8 during snapshot processing without
  modifying the source or preventing valid conversations from being summarized.
- Direct targeted reads in file-editing guidance to the configured command tool.

- Preserve conversation history when compaction cannot produce a replacement within its context budget, and report current usage and headroom after successful compaction.

## 0.1.0a1

Initial independently installable package. See README for capabilities and setup.
