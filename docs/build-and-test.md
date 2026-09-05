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
release_dir="$(mktemp -d)"
uv build --no-sources --all-packages --out-dir "$release_dir"
uv run twine check "$release_dir"/*
uv run python scripts/check_distributions.py --dist "$release_dir"
```

The last command installs built wheels into temporary environments outside the
checkout and can download dependencies. It checks core, each companion, and combined extras in separate pip environments.
It verifies import locations, dependency consistency, metadata, licenses, and
`py.typed`, then repeats installations with wheels rebuilt from source archives
using `uv build --no-sources`. Providers are scripted; no credentials are needed.

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

## CI parity and branch protection

Use the aggregate **CI** status as the required branch-protection check in GitHub.
It requires every job in `.github/workflows/verify.yml` to succeed; failures,
cancellations, and skipped required jobs cannot produce a green aggregate.
Pull requests (including forks), pushes to `main`, and manual dispatch run the
same validation. Only the separately triggered release job can publish.

- Python 3.13 and 3.14 run all core and companion tests and the quickstart.
- Quality runs Ruff, Pyright, and positive/negative typing contracts once.
- Distribution builds fresh wheels and source archives, checks them with Twine,
  and verifies independent pip installs outside the checkout, including E2E.
- Windows and macOS install the core wheel and run its portable workflows.
  Guarded Unix file-command E2E remains a Linux check; this does not claim all
  companion tools support Windows or macOS.
- Downstream builds RoboSprawl at `7f956cb6cfd93db0cc1f95c7dcffc9eed0e7e8b5`,
  installs it with candidate Roboz wheels, and exercises composition and a real
  HTTP create/stream/reply/completion cycle. The checksum-verified
  [source fixture](../tests/fixtures/robosprawl/README.md) preserves the upstream
  Python source and composition test. A pin update needs review and the same
  contract checks.

CI pins uv 0.12.10 and full commit SHAs for actions. Dependabot maintains action
pins. Workflow permissions are read-only, checkouts do not retain credentials,
jobs have deadlines, and newer CI runs cancel superseded runs. These choices
follow [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).
No provider credentials or publication secrets are available to PR checks.
The downstream source fixture requires no cross-repository credentials, so the
same gate also runs for Dependabot and fork pull requests.

JUnit reports, coverage, candidate distributions, and downstream backend logs
are retained for 14 days. Local logs and runner results are evidence of local
checks, not evidence that GitHub Actions or nonlocal platforms have passed.

## Coverage and deterministic end-to-end tests

Run the statement coverage gates independently for each distribution:

```bash
uv run pytest --cov=roboz_shed --cov=roboz_openai --cov=roboz_proton_bridge \
  --cov-report=xml:reports/coverage.xml --cov-report=json:reports/coverage.json \
  --junitxml=reports/pytest.xml
uv run python scripts/check_coverage.py reports/coverage.json
uv run pytest tests/e2e --no-cov
```

Floors are core 95%, Shed 90%, OpenAI 90%, and Proton Bridge 89%. They are
statement coverage, compared without rounding; high coverage in another package
cannot compensate for a failure. Missing package coverage also fails.

`tests/e2e/` scripts exercise real agents/tools/events/persistence with scripted
provider boundaries. They cover guarded read/edit/read, traversal and symlink
escape denial, parent/child completion, and conversation → snapshot → memory →
retention. The same files are copied outside the checkout and executed by the
installed interpreter, so source-tree imports cannot satisfy the install gate.

To reproduce the downstream gate with the pinned fixture:

```bash
(cd tests/fixtures/robosprawl && sha256sum --check SHA256SUMS)
downstream_dir="$(mktemp -d)"
tar -xzf tests/fixtures/robosprawl/source.tar.gz --directory "$downstream_dir"
uv run python scripts/check_downstream.py --source "$downstream_dir" --dist "$release_dir"
```

To test a reviewed RoboSprawl checkout before updating the fixture:

```bash
uv run python scripts/check_downstream.py --source ../robosprawl --dist "$release_dir"
```

The script installs candidates and the application wheel with pip, checks
imports originate in that environment, and runs copied composition tests plus
the HTTP contract in an isolated config/data directory. It uses pip's resolver
to test declared consumer requirements; `uv.lock` governs development checks,
not consumers' independent installations.

## Release artifacts and PyPI transition

The release workflow calls the same reusable validation as CI, then selects the
tagged distribution from the exact verified artifact bundle using:

```bash
uv run python scripts/release_package.py roboz-v0.1.1 --check
# On an explicitly authorized release only, with already verified artifacts:
uv run python scripts/release_package.py roboz-v0.1.1 --from-dist "$release_dir"
```

`--from-dist` copies both archives byte-for-byte and refuses an occupied release
output directory; it never rebuilds after verification. Tags and publication
still require explicit maintainer action. This change creates neither.

Python distribution is pip/PyPI. Local workspace/source overrides are temporary
development scaffolding. Publish dependencies before removing RoboSprawl's
three local source overrides, refresh its lock, and rerun the same archive and
installation gates with the intended published artifacts. Do not rewrite source
paths inside CI or replace wheel verification with editable installs. See
[uv packaging guidance](https://docs.astral.sh/uv/guides/package/) for checking
builds with sources disabled. Containerization is outside this change.
