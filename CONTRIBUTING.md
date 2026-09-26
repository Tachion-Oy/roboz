# Contributing to RoboZ

Bug reports, fixes, examples, and documentation improvements are welcome.

## Choose the contribution path

- **Small fixes may go directly to a pull request:** typos, broken links, small
  documentation corrections, and obvious, narrowly scoped bug fixes that
  restore expected behavior without API or design changes.
- **Substantive changes require an approved issue first:** new features, new or
  changed public APIs, intentional behavior changes, significant bug fixes,
  refactors, architectural changes, performance rewrites, new dependencies,
  and breaking changes.

For substantive changes, search
[existing issues](https://github.com/Tachion-Oy/roboz/issues), then open an issue
or join the relevant discussion. Explain the problem and proposed approach.
Wait for a maintainer to explicitly accept the proposal for implementation
before starting work or opening a substantial PR. Opening an issue is not
approval. If unsure which path applies, ask in an issue first.

Substantive changes follow this flow:

`idea / bug → issue → maintainer triage → approval → implementation → PR → CI → review → merge`

Maintainers may close unapproved implementations without detailed review; they
are not obliged to review substantial work on a proposal they have not accepted.
For security vulnerabilities, use the private process in [SECURITY.md](SECURITY.md).

## Getting started

Use Python 3.13 or 3.14 and
[uv](https://docs.astral.sh/uv/getting-started/installation/). Fork and clone the
repository, then install the development environment from its root:

```bash
uv sync --locked --dev
uv run python -m roboz.examples.simple
```

The development environment includes core, Shed, Endpoints, and the OpenAI SDK.
The example and default test suite require no API keys or live services.

Before changing code, read the [code style guide](docs/code-style.md) and
[testing practices](docs/testing-practices.md).

## Testing

Include appropriate tests for changed behavior and bug fixes, following
[testing practices](docs/testing-practices.md). Run the checks relevant to your
change. The standard local checks are:

```bash
uv run pytest
uv run python -m roboz.examples.simple
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
git diff --check
```

For dependency changes, run `uv lock` and include the reviewed lockfile changes.

CI also builds and checks distributions, tests independent installations, and
runs portable core checks. The
[verification workflow](.github/workflows/verify.yml) is the source of truth for
the complete gate. All applicable automated checks, including linting and type
checking, and the aggregate **CI** check must pass before merging.

## Simpsons quote

Each PR should add one more Simpsons quote to the
[example quote module](src/roboz/examples/simpsons_quotes.py), no exceptions :)

## Opening a pull request

Each PR should solve one problem. Substantive PRs must stay within the approved
scope and link their issue; discuss changes to the approach in the issue first.
Use `Fixes #123` or `Closes #123` when merging the PR should resolve the issue.
Small fixes do not require an issue.

Do not include unrelated refactoring, formatting, dependency changes, cleanup,
or speculative improvements. Summarize the change and testing performed,
including checks you could not run. Update affected examples and documentation,
and explain how users should adapt to any breaking changes.

Add a short entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for
user-visible changes. Documentation, tests, and internal cleanup generally do
not need entries. Leave version bumps, tags, and publication to maintainers.

## Responsibility and review

You are responsible for everything you submit, including code produced by AI,
which, to emphasize, **is completely allowed :)**. Understand, review, and
validate your changes, and be able to explain and justify the implementation. Generated code must meet the
same standards as manually written code. Submitting a large amount of generated
code does not transfer the work of understanding or validating it to maintainers.
Low-quality, speculative, mass-generated, or spam-like contributions may be closed.

Passing CI is necessary but not sufficient for acceptance. Maintainers retain
final discretion over scope and design and may reject changes that do not fit
the project's architecture, API design, maintainability standards, or long-term
direction. Contributions are welcome, but issue approval or submitting a PR
does not create an obligation to merge it.

## Reporting bugs

[Open an issue](https://github.com/Tachion-Oy/roboz/issues) with a minimal
reproduction, expected and actual behavior, package and Python versions, and
your operating system. Remove credentials and private data from logs and
examples. Report security vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).
