# Contributing

Use Python 3.13 or 3.14 and uv 0.12.10. Start with `uv sync --locked --dev`.
Follow [repository instructions](AGENTS.md), [testing practices](docs/testing-practices.md),
and the [complete CI-equivalent commands](docs/build-and-test.md).

Keep changes scoped to the owning distribution. Add focused regression tests;
update affected public documentation and Unreleased changelogs when behavior
changes. Do not bump versions or publish as part of ordinary development.

Require the aggregate **CI** check before merging. It covers all library
packages, independent installations, portable core smoke tests, and the pinned
downstream application contract. Report local validation separately from
GitHub-run results. No live credentials are needed by the default suite.
