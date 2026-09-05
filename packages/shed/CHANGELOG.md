# Changelog

## Unreleased

### Added

- Compact active conversations with configurable continuation prompts, context thresholds, cancellation, and optional per-attempt timeouts.

### Changed

- Document Shed's agent-facing tools under the shared docstring contract.

### Fixed

- Preserve conversation history when compaction cannot produce a replacement within its context budget, and report current usage and headroom after successful compaction.

## 0.1.0a1

Initial independently installable package. See README for capabilities and setup.
