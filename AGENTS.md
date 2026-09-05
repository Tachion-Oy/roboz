# Repository Instructions

These instructions apply to the entire Roboz repository. A change is complete
only when the affected Python distributions remain suitable for release.

## Repository and Release Boundaries

This workspace contains four independently versioned distributions:

| Distribution | Import package | Changelog |
| --- | --- | --- |
| `roboz` | `roboz` | `CHANGELOG.md` |
| `roboz-shed` | `roboz_shed` | `packages/shed/CHANGELOG.md` |
| `roboz-openai` | `roboz_openai` | `packages/openai/CHANGELOG.md` |
| `roboz-proton-bridge` | `roboz_proton_bridge` | `packages/proton-bridge/CHANGELOG.md` |

Before editing, inspect the working tree and identify every affected
distribution. Preserve existing user changes and keep the requested scope.
Report unrelated release or open-source readiness gaps instead of fixing them
without authorization.

Do not bump versions, change compatibility ranges, create tags, publish
packages, or perform other release actions unless the task explicitly requests
release preparation. Ordinary user-visible changes belong under `Unreleased`
in each affected distribution's changelog. Purely internal, test-only, and
documentation-only changes do not need a changelog entry.

## Definition of Done

For every change, check all of the following that the change can affect:

- Preserve compatibility of public imports, signatures, typing, Pydantic
  schemas, serialized or persisted data, prompts, defaults, and observable side
  effects. Do not introduce a breaking change without explicit authorization
  and migration documentation.
- Keep the core package independent of companion and provider dependencies.
  Optional integrations must remain in their owning distributions, and
  published requirements must not contain workspace paths, local file URLs, or
  undeclared imports.
- Keep package metadata accurate, including supported Python versions,
  dependencies, README and license metadata, classifiers, package contents,
  and the PEP 561 `py.typed` marker. Update `uv.lock` whenever dependency or
  relevant project metadata changes.
- Add focused regression or contract tests for changed behavior. Add cases to
  `tests/type_tests/` when the contract is enforced by the type checker. Tests
  must be deterministic and must not require live credentials or services in
  the default suite.
- Update public documentation, examples, and package READMEs when behavior or
  usage changes. Follow `docs/docstrings.md` for shipped source. In particular,
  the complete normalized docstring of an `@tool` or `@factory` callable is
  model-facing API text and must be written as an accurate agent instruction.
- Add a concise, user-centered `Unreleased` changelog entry to every affected
  distribution when the change is externally observable. Call out breaking
  changes and their migration path.
- Avoid unrelated cleanup. Never hide a failing gate, weaken validation, or
  remove a test merely to make a change pass.

Detailed policy lives in:

- `docs/maintainer-basics.md`
- `docs/build-and-test.md`
- `docs/testing-practices.md`
- `docs/tool-authoring.md`
- `docs/docstrings.md`

## Required Validation

Any change to shipped Python source, dependencies, project metadata, public
APIs, persisted formats, prompts, or runtime behavior requires the full release
gate before completion:

```bash
uv sync --locked --dev
uv run pytest
uv run python examples/quickstart.py
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh

release_dir="$(mktemp -d)"
uv build --all-packages --out-dir "$release_dir"
uv run twine check "$release_dir"/*
uv run python scripts/check_distributions.py --dist "$release_dir"
```

If project metadata or dependencies changed, run `uv lock` first, review the
lockfile diff, and then use the locked sync above. Build into a fresh temporary
directory so previous artifacts cannot satisfy the checks and user-owned
artifacts are not overwritten.

Documentation-only, test-only, and repository-maintenance changes may use
proportionate validation, but must at minimum run the directly relevant checks
and `git diff --check`. If the working tree also contains shipped-source
changes, run the full gate for the combined worktree.

Some distribution checks may need package-index access. If a required command
cannot run because of network, credentials, platform, or environment limits,
state exactly what was not verified. Do not describe the change as fully
release-ready until all required gates have passed, either locally or in CI.

## Release Preparation

When explicitly preparing a release, additionally:

1. Move the affected changelog entries from `Unreleased` under the chosen
   version and actual release date, leaving an empty `Unreleased` section.
2. Update the affected `pyproject.toml`, compatible inter-package bounds, and
   `uv.lock` together.
3. Validate the intended tag with
   `uv run python scripts/release_package.py <distribution>-v<version> --check`.
4. Re-run the full release gate on the reviewed release commit.

Tag creation and publication remain explicit maintainer actions. Successful
validation alone does not authorize either action.

## Handoff

When reporting completion, summarize affected distributions and user-visible
behavior, list documentation and changelog updates, and report every validation
command and result. Clearly identify any skipped or blocked check and its
impact on release confidence.

