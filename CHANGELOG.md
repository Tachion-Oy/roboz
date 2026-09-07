# Changelog

## Unreleased

### Added

- Add `ExternalDependencyReference` for replaceable resources. Endpoint helpers
  preserve live selection and discovery through contexts, tools, and agents,
  while lazy resources retain their identity validation and cached clients.

- Inspect resources before binding tools with `Ctx.external_dependencies()`.
  Contexts implement `ExternalDependencySource`, preserving resource identity
  and reflecting live nested contexts, agents, and catalogs without resolving
  lazy resources. `external_dependencies` is now a reserved context field name;
  see `docs/context-migration.md`.

### Fixed

- Refresh lifecycle endpoint metadata when invoking an agent again after a live
  reference switches models, while retaining each lazy dependency's client cache.

### Changed

- **Breaking:** Construct tool contexts with `Ctx(**values)` and pass resources
  directly. Remove `FactoryCtx`, specialized context classes, `ToolDependency`,
  and endpoint binding wrappers; `Tool.dependencies` now returns resources.
  See `docs/context-migration.md` for replacements and state ownership.

- Define and enforce Google-style source docstrings, and normalize tool
  docstrings before including them in agent prompts.

## 0.1.1

- Preserve concrete nested model types across in-memory tool handoffs while
  keeping field projection, excluded fields, validation, and detached inputs.
- Add optional companion extras and independent package release workflows.
- Keep the core's runtime dependency set unchanged.
