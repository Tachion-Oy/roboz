# Changelog

## Unreleased

- Breaking: capability builders receive `build(pipe, *, default_endpoint)`.
  Configure tool-specific endpoints on capabilities and use the owning agent's
  model as a fallback. Endpoint objects and runtime controls remain independent.
  See `docs/agent-factories.md` for migration.

- Add `roboshed.deployments.robosprawl.AgenticFactory` to bind project persistence, initial memory, and background-start capabilities to configured definitions. Builds return `RoboSprawlBundle(agent, background_agents)`, a named tuple for direct invocation and host cancellation; construction starts no threads. See `docs/agent-factories.md`.

- Breaking: own orchestrator/Librarian presets, workspace/project structure, reusable capabilities, and memory/summarization tools. Import presets from `roboshed.agents`, feature implementations from `roboshed.capabilities`, and explicit paths/permission inputs from `roboshed.workspace`. Both presets return `AgentDefinition`; the orchestrator remains available across tasks until asked to stop. Assistant extensions use only `capabilities`; replace `workspace` with `project` and optional `permissions`, and move separate tool/skill inputs into capability builds. See `docs/agent-factories.md`.

- Breaking: rename the distribution from `roboz-shed` to `roboshed` and the import namespace from `roboz_shed` to `roboshed`. Update dependency declarations and imports; the `roboz-demo` command is unchanged. No compatibility package is provided.

### Added

- Compact active conversations with configurable continuation prompts, context thresholds, cancellation, and optional per-attempt timeouts.

### Changed

- **Breaking:** Replace specialized tool contexts with `roboz.Ctx(**values)`
  and use service/executable resources directly. Existing builders retain their
  defaults and guard behavior. See `docs/context-migration.md` in the repository
  for the low-level API migration.

- Document Shed's agent-facing tools under the shared docstring contract.

### Fixed

- Preserve conversation history when compaction cannot produce a replacement within its context budget, and report current usage and headroom after successful compaction.

## 0.1.0a1

Initial independently installable package. See README for capabilities and setup.
