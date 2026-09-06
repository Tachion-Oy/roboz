# Changelog

## Unreleased

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
