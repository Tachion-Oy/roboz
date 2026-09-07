# Changelog

## Unreleased

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
