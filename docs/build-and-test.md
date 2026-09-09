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
uv run check-wheel-contents "$release_dir"
uv run pytest tests/distributions --no-cov --dist "$release_dir"
```

The last command installs built wheels into temporary environments outside the
checkout and can download dependencies. It checks core, each companion, and combined extras in separate pip environments.
It verifies import locations, dependency consistency, metadata, licenses, and
`py.typed`. The normal `uv build --no-sources` builds each wheel from its source
archive. Endpoint installs cover both the SDK-free base and
the `[openai]` extra; the latter runs real SDK contracts with simulated HTTP.
Both endpoint installations also run consumer typing checks against the installed
package, covering named model types and invalid model use.
Providers are scripted; no credentials are needed. Use the CI uv version
(currently 0.12.10) when regenerating `uv.lock` to avoid unrelated lock-format changes.

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

### Endpoint catalogue generation

Model records in `packages/endpoints/src/roboz_endpoints/inventory.py` are the
source for runtime model attributes and generated typing declarations that Pylance
reads. After changing those records, or public catalogue signatures or docstrings,
run from the repository root:

```bash
uv run python scripts/generate_endpoint_catalog.py
uv run python scripts/generate_endpoint_catalog.py --check
```

Review and commit the inventory and generated `catalog.pyi`, `inventory.pyi`,
and `__init__.pyi` together. Do not maintain individual model properties or edit
these generated files by hand. Provider definitions also belong in the inventory;
the generator discovers them from its `CATALOGS` mapping. Update the endpoint
README's model table and relevant runtime/typing tests with model changes.

`--check` fails on missing or stale output without writing files. The normal
pytest suite checks freshness, including in CI. Wheels and source archives ship
the generated files; imports and builds do not regenerate them, and consumers need
no generator or editor configuration. See the
[endpoint maintenance instructions](../packages/endpoints/README.md#maintain-the-bundled-inventory).

Project inventories use the packaged `roboz-endpoints inventory export`, `import`,
and confirmed `reset` commands. These generate a separate application module;
they never change the bundled data or stubs. Both generators share provider type
declaration rendering. After changing that rendering, regenerate the committed
consumer typing fixture as well as checking the bundled declarations:

```bash
uv run roboz-endpoints inventory import --path tests/type_tests/fixtures/models.json \
  --output tests/type_tests/fixtures/inventory_models.py --force
uv run python scripts/generate_endpoint_catalog.py --check
```

The inventory tests check fixture freshness and SDK-free operation. The installed
distribution checks also exercise console entry points, JSON round trips,
generated consumer typing, reset cancellation, and confirmed restoration outside
the checkout for the base wheel, SDK extra, and source-archive rebuilds.

### Unit test suite

```bash
uv run pytest
```

Notes:

- pytest configuration lives in `pyproject.toml`
- type tests and distribution installation tests are excluded from default pytest
  runs and are executed separately
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
same validation. The shared workflow is named **Tests and packaging**. Only the
separately triggered package-tag release workflow can publish, after production
approval. There is no TestPyPI stage or manual publication dispatch.

- Python 3.13 and 3.14 run all core and companion tests and the quickstart.
- Quality runs Ruff, Pyright, and positive/negative typing contracts once.
- Distribution builds fresh wheels and source archives, checks them with Twine,
  and verifies independent pip installs outside the checkout, including E2E.
- Windows and macOS install the core wheel and run its portable workflows.
  Guarded Unix file-command E2E remains a Linux check; this does not claim all
  companion tools support Windows or macOS.

Roboz CI validates the core primitives and companion distributions in this
workspace. Consumer applications own their integration checks in their own
repositories; Roboz validation requires no application checkout or source copy.

CI pins uv 0.12.10 and full commit SHAs for actions. Dependabot maintains action
pins. Workflow permissions are read-only, checkouts do not retain credentials,
jobs have deadlines, and newer CI runs cancel superseded runs. These choices
follow [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).
No provider credentials or publication secrets are available to PR checks.
No cross-repository credentials are required for Dependabot or fork pull requests.

JUnit reports, coverage, and candidate distributions are retained for 14 days.
Local logs and runner results are evidence of local checks, not evidence that
GitHub Actions or nonlocal platforms have passed.

## Coverage and deterministic end-to-end tests

Collect statement coverage for all four distributions:

```bash
uv run pytest --cov=roboshed --cov=roboz_endpoints --cov=roboz_proton_bridge \
  --cov-report=xml:reports/coverage.xml --cov-report=json:reports/coverage.json \
  --junitxml=reports/pytest.xml
uv run pytest tests/e2e --no-cov
```

Coverage reports help identify missing tests. There are no fixed percentage
thresholds; failing tests still fail CI.

`tests/e2e/` scripts exercise real agents/tools/events/persistence with scripted
provider boundaries. They cover guarded read/edit/read, traversal and symlink
escape denial, parent/child completion, and conversation → snapshot → memory →
retention. The same files are copied outside the checkout and executed by the
installed interpreter, so source-tree imports cannot satisfy the install gate.

## Installed-distribution checks

Run `uv run pytest tests/distributions --no-cov --dist DIR` against a fresh
candidate directory. Add `--package roboz` (or another distribution name) to
select a package. Missing wheel/sdist pairs fail the check.

The suite owns a small environment fixture and normal consumer tests. It copies
only the relevant tests into a temporary directory and runs them using the
installed interpreter, isolated from the checkout and development configuration.
Pip checks dependencies; the tests check imports, optional SDK isolation, lazy
endpoints, package metadata and typing files. Existing E2E and adapter contracts
run against the installed packages with simulated provider boundaries.

Default pytest runs omit this directory, so they neither create consumer
environments nor download dependencies. The explicit installation suite may
download from PyPI and needs network access.

## Preparing and publishing a release

Choose the package version, update its changelog and any required dependency
bounds, then run `uv lock`. Review the release notes and date manually, leaving
an empty Unreleased section. The tag checker only verifies package/version
agreement; it does not parse the changelog.

```bash
# Use the actual version selected for release:
uv run python scripts/release_package.py roboz-endpoints-v0.1.0a2 --check
uv lock --check
```

After review, a pushed `<distribution>-v<version>` tag runs:

1. Tag/version validation and the complete shared verification workflow.
2. Installation of the selected candidate wheel with dependencies from PyPI.
3. Selection of its already-tested wheel and sdist without rebuilding.
4. The configured `pypi` environment approval, followed by Trusted Publishing.

The selected-wheel check can also run locally:

```bash
uv run pytest tests/distributions --no-cov --dist "$release_dir" \
  --package roboz-endpoints --published-dependencies
```

This mode installs only the selected candidate; companion dependencies come
from PyPI, independently of workspace overrides and the development lockfile.
Publish dependency releases first: core before Shed/Endpoints, then core and
Shed before Proton. An unavailable or incompatible dependency fails this check.

There is no TestPyPI stage, index client, or automatic post-publication polling.
The standard PyPA publishing action uploads the verified archives and rejects
duplicates. A publication does not become reversible because a release is alpha
or development. If necessary, prepare a corrected version and consider yanking
the old release. Re-running the failed publishing job is appropriate only when
no conflicting files have already been uploaded.

Configure the PyPI Trusted Publisher and GitHub approval environment as described
in [maintainer basics](maintainer-basics.md#publisher-setup). A successful local
test run does not establish those external permissions, and makes no upload.

When editing workflows, run `actionlint .github/workflows/*.yml`. Focused
regression tests are `tests/test_release_package.py` and
`tests/test_release_workflows.py`; they require no live services. Historical
verification reports describe earlier tooling and are not current instructions.
