# Repository Instructions

Read and follow the documents relevant to the task before editing:

- [CONTRIBUTING.md](CONTRIBUTING.md): issue approval, environment setup,
  validation commands, changelogs, and the PR process.
- [Code style](docs/code-style.md): Python conventions, typing, package
  boundaries, and docstrings that become model prompts. Read for source changes.
- [Testing practices](docs/testing-practices.md): test scope, mocking boundaries,
  and runtime versus typing tests. Read when changing behavior or tests.
- [README.md](README.md): core concepts, runnable examples, and the package map.
  For companion changes, also read the owning package's README:
  [Shed](packages/shed/README.md), [Endpoints](packages/endpoints/README.md), or
  [Proton Bridge](packages/proton-bridge/README.md).
- [Endpoint catalogue guide](packages/endpoints/docs/catalogues.md): project
  catalogue layout, inventory commands, custom locations, reset, and recovery.
- [Verification workflow](.github/workflows/verify.yml): full CI commands,
  packaging checks, and supported test environments. Consult for changes to
  dependencies, packaging, or CI.
- [SECURITY.md](SECURITY.md): private vulnerability reporting.

Inspect the working tree and preserve existing user changes. A direct maintainer
request authorizes local work; the issue requirement governs community PRs.
Do not create issues or PRs, bump versions, tag, or publish unless requested.
At handoff, state what changed and which checks passed, failed, or were not run.
