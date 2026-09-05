# Build and Test Guide

The root uv workspace includes all companion projects for development. The
default pytest and Pyright commands cover core plus companion tests/source;
runtime installs of core still include no companion dependencies.

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check
uv run pyright
bash scripts/run_type_tests.sh
uv build --all-packages --out-dir dist/first-slice
uv run twine check dist/first-slice/*
uv run python scripts/check_distributions.py --dist dist/first-slice
```

The last command installs built wheels into temporary environments outside the
checkout and can download dependencies. It checks base/Shed isolation, the mock
assistant, Proton without other SDKs, the model adapter, and extras resolution.

This page centralizes local build/test validation for contributors.
It mirrors the repository CI flow.

Primary references:

- project metadata/dependencies: [`../pyproject.toml`](../pyproject.toml)
- CI workflow: [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)
- type-test script: [`../scripts/run_type_tests.sh`](../scripts/run_type_tests.sh)
- testing practice: [`testing-practices.md`](testing-practices.md)

## Prerequisites

- Python version from `pyproject.toml` (`>=3.13`)
- `uv` installed locally

## Environment Setup

Install dependencies exactly as CI does:

```bash
uv sync --locked --dev
```

## Test Commands

Follow [`testing-practices.md`](testing-practices.md) when adding or changing
tests. The commands below validate the suite; they do not replace the need for
focused tests with minimal, well-scoped mocking.

### Unit test suite

```bash
uv run pytest
```

Notes:

- pytest configuration lives in `pyproject.toml`
- type tests are excluded from default pytest run and are executed separately
- Ruff enforces the [docstring guide](docstrings.md) on shipped source; tests,
  examples, and maintenance scripts are exempt from docstring-only rules

### Type tests

First run the release-gating source check:

```bash
uv run pyright
```

The main Pyright configuration checks `src/` in standard mode. Standard mode is
the release gate for now; migrating the package to strict mode and full
`--verifytypes` completeness is deferred follow-up work.

Then run the dedicated type-test harness:

```bash
bash scripts/run_type_tests.sh
```

This script enforces:

- scenarios in `tests/type_tests/cases/valid/` must pass pyright
- scenarios in `tests/type_tests/cases/expected_failures/` must fail pyright
- expected-failure scenarios should be one file per typing rule so diagnostics stay precise

Optional verbose diagnostics:

```bash
TYPE_TESTS_VERBOSE=1 bash scripts/run_type_tests.sh
```

## Recommended Local Validation Order

1. Sync dependencies (`uv sync --locked --dev`)
2. Run pytest (`uv run pytest`)
3. Run the quick start (`uv run python examples/quickstart.py`)
4. Run Ruff (`uv run ruff check`)
5. Type-check the source (`uv run pyright`)
6. Run type tests (`bash scripts/run_type_tests.sh`)
7. Build the artifacts (`uv build`)
8. Check their metadata (`uv run twine check dist/*`)

For a release candidate, also install the wheel into a fresh environment and
verify that `import roboz` succeeds. The wheel must contain `roboz/py.typed` and its
license metadata, and its core metadata must include `Import-Name: roboz` and
`License-File: LICENSE`.

## CI Parity

CI has three jobs:

- `tests`: runs pytest and the quick-start example on Python 3.13 and 3.14
- `quality`: runs Ruff, the source check, and the dedicated type tests
- `distribution`: builds and validates both artifacts on Python 3.13, then
  installs the wheel into an isolated environment and smoke-tests `import roboz`

Reference workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
