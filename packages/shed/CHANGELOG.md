# Changelog

## Unreleased

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
  consolidation can use separate models. Use `librarian(agent_endpoint=...)`
  for a shared default, with `endpoint` overrides on `ConversationSnapshots`
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

- Reject `grep -R`/`--dereference-recursive` and `rg -L`/`--follow` before
  execution so recursive search cannot use these options to read through nested
  symlinks outside guarded paths. **Compatibility:** use `grep -r`/`--recursive`
  or plain `rg`; pass intended symlink targets explicitly for permission checks.
- Check CLI file operands across `--`, trailing options, and search-pattern
  options while preserving stdin markers. Reject unsupported/abbreviated options,
  auxiliary file inputs, and preprocessors before execution. **Compatibility:**
  see the README's file CLI migration guidance for the stricter supported grammar.
  Recursive descendant authorization remains separate from this parser fix.
- Respect copy/move destination modes when checking permissions, including `--`
  and `-T`. Reject unmatched path globs before execution instead of dropping them
  from argv; see the README's file CLI migration guidance.
- Keep CLI execution consistent with validated arguments by ignoring
  `POSIXLY_CORRECT` and `RIPGREP_CONFIG_PATH`. Pass supported options explicitly
  in argv; see the README's environment compatibility guidance.

- Reject relative project persistence paths that escape through traversal or
  symlinks. External logs, snapshots, and memory require explicit absolute paths;
  see `docs/agent-factories.md` for migration guidance.
- Skip conversation files with invalid UTF-8 during snapshot processing without
  modifying the source or preventing valid conversations from being summarized.
- Direct targeted reads in file-editing guidance to the configured command tool.

- Preserve conversation history when compaction cannot produce a replacement within its context budget, and report current usage and headroom after successful compaction.

## 0.1.0a1

Initial independently installable package. See README for capabilities and setup.
