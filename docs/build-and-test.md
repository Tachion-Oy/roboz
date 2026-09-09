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
using `uv build --no-sources`. Endpoint installs cover both the SDK-free base and
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
[endpoint maintenance instructions](../packages/endpoints/README.md#maintain-the-inventory).

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
same validation. The shared workflow is named **Tests and packaging**. Only the
separately triggered release workflow can publish; its manual runs publish to
TestPyPI only, while package tags can proceed to production approval.

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

Run the statement coverage gates independently for each distribution:

```bash
uv run pytest --cov=roboshed --cov=roboz_endpoints --cov=roboz_proton_bridge \
  --cov-report=xml:reports/coverage.xml --cov-report=json:reports/coverage.json \
  --junitxml=reports/pytest.xml
uv run python scripts/check_coverage.py reports/coverage.json
uv run pytest tests/e2e --no-cov
```

Floors are core 95%, Shed 90%, Endpoints 90%, and Proton Bridge 89%. They are
statement coverage, compared without rounding; high coverage in another package
cannot compensate for a failure. Missing package coverage also fails.

`tests/e2e/` scripts exercise real agents/tools/events/persistence with scripted
provider boundaries. They cover guarded read/edit/read, traversal and symlink
escape denial, parent/child completion, and conversation → snapshot → memory →
retention. The same files are copied outside the checkout and executed by the
installed interpreter, so source-tree imports cannot satisfy the install gate.

## Release preparation and TestPyPI rehearsals

### RoboSprawl integration rehearsal

This branch prepares `roboz==0.1.2.dev2`, `roboshed==0.1.0a2`, and
`roboz-endpoints==0.1.0a2` for TestPyPI. Run the existing manual release workflow
from the same preparation commit for core first, then Shed and Endpoints. The
Proton Bridge distribution is validated with the workspace but is not published
as part of this rehearsal. Production tags are not needed.

Register and publish the new companion projects **one at a time**. TestPyPI
permits only one pending publisher for the same owner/repository/workflow/
environment combination, even when the proposed project names differ:

1. At https://test.pypi.org/manage/account/publishing/, add a pending publisher
   for one companion: owner `Tachion-Oy`, repository `roboz`, workflow
   `release.yml`, environment `testpypi`.
2. Run the release workflow for that companion and wait for successful
   publication. Its pending publisher becomes a normal publisher.
3. Add the other companion's pending publisher with the same configuration,
   then publish that companion. Either companion can go first after core.

If a pending registration already exists, publish that named package first;
do not add another matching pending publisher or delete the working core
publisher. The existing `roboz` registration remains usable. See
[PyPI's explanation of the pending-identity constraint](https://github.com/pypi/warehouse/issues/20006).

After all three runs succeed, a fresh Python 3.13+ environment can install the
published wheels with pip. Download only the named Roboz packages from TestPyPI;
resolve their ordinary dependencies on PyPI:

```bash
rehearsal_wheels="$(mktemp -d)"
python -m pip --isolated download --no-deps --only-binary=:all: \
  --index-url https://test.pypi.org/simple/ --dest "$rehearsal_wheels" \
  roboz==0.1.2.dev2 roboshed==0.1.0a2 roboz-endpoints==0.1.0a2
python -m pip --isolated install --index-url https://pypi.org/simple/ \
  "$rehearsal_wheels/roboz-0.1.2.dev2-py3-none-any.whl" \
  "$rehearsal_wheels/roboshed-0.1.0a2-py3-none-any.whl" \
  "$rehearsal_wheels/roboz_endpoints-0.1.0a2-py3-none-any.whl[openai]"
python -m pip check
```

RoboSprawl automates equivalent downloads with lockfile hash verification. Its
normal `scripts/install.sh` uses a named, explicit TestPyPI index for the three
packages; no Roboz source checkout or publishing credential is needed.

### Preparation checks

Prepare the repository before tagging or starting a rehearsal. Choose the version
in the selected distribution's `pyproject.toml`, update affected dependency bounds,
write the release notes, run `uv lock`, and commit the reviewed preparation.
Version choice and the accuracy of release notes remain maintainer judgments.
The workflow never changes versions or infers patch/minor/major compatibility.

The selected changelog must start with an empty `## Unreleased` section, followed
by `## <version> - YYYY-MM-DD` and nonempty release notes. Brackets around the
version are also accepted. Dates must be valid and not in the future (UTC).
Other packages may still have Unreleased changes. Historical undated entries do
not need rewriting, but the selected release must have a date. Existing staged
versions are not automatically release-prepared by this tooling change.

Check preparation locally before committing or tagging (use the version you
actually prepared):

```bash
uv run python scripts/release_package.py --package roboz-endpoints --check
uv lock --check
# Example after preparing version 0.1.0a2:
uv run python scripts/release_package.py roboz-endpoints-v0.1.0a2 --check
```

The release workflow first checks preparation and lockfile consistency. For a
tagged release it also rejects a version already present on PyPI. Only then does
it call the complete shared verification workflow. It selects the package from
the verified candidate bundle with `release_package.py <tag> --from-dist <dir>`.
Selection copies its wheel and source archive byte-for-byte, refuses an occupied
output directory, and does not rebuild them.

To rehearse without production publication, open **Actions → Release one Python
package → Run workflow**, choose the prepared branch/ref and one package, and
start the run. The workflow must first be present on the default branch for the
manual run button to appear. The run uploads only that package to TestPyPI,
downloads and checks it, then ends. It has no input that enables production.

Rehearsals use declared versions, including explicitly chosen development or
prerelease versions on a rehearsal branch. They do not generate temporary versions.
Stage the required Roboz packages first, at the versions declared in the rehearsal
checkout: core before Shed/Endpoints, then core and Shed before Proton Bridge.
The checker downloads those dependency wheels explicitly from TestPyPI and uses
PyPI only for third-party dependencies. It does not use a combined index search
or the candidate bundle to satisfy Roboz dependencies. Pip still checks that
the staged versions satisfy the selected package's declared bounds.

Tagged releases follow this route:

```text
Preparation → full verification → select package → TestPyPI upload
  → download/install/contracts with real PyPI dependencies
  → pypi environment approval → PyPI upload → download/install/contracts
```

Before production upload, the selected TestPyPI wheel must install and pass its
contracts with dependencies from real PyPI. For example, Endpoints cannot rely on
an unpublished core API. Publish required dependency releases first. The final
upload uses the same selected wheel and source archive, with no intervening build.

Both index checks download the exact wheel and source archive and compare their
SHA-256 hashes with the verified candidates. The wheel is installed into a new
environment outside the checkout, with pip configuration/source overrides cleared.
Checks cover `pip check`, version/import locations, core or Shed workflows and
the Shed CLI, or the Endpoints/Proton adapter contracts using fake HTTP/IMAP services.
The dependency installation is independent of `uv.lock`; default development
and CI tests continue to use the lockfile.

Index availability is retried for up to three minutes. Hash conflicts and failed
package contracts fail immediately. An existing TestPyPI archive is reused only
when its hash matches; missing archives in a matching partial upload are staged
for upload. Both files are downloaded and verified afterward, including when no
upload was needed. Changed contents under an existing filename require a new
version, even if the old file was deleted. Production duplicates fail instead of
being skipped. After a successful upload followed by a failed check, rerun the
failed verification job; do not start another production release of that version.

The index checker can also be run against saved candidate artifacts:

```bash
# Anonymous read-only checks; these commands never upload.
uv run python -m scripts.check_published_package --package roboz-endpoints \
  --index testpypi --dependency-index testpypi --candidates "$release_dir"
uv run python -m scripts.check_published_package --package roboz-endpoints \
  --index pypi --dependency-index pypi --candidates "$release_dir"
```

Run `actionlint .github/workflows/*.yml` when editing workflows. The focused
regression tests are `tests/test_release_package.py`,
`tests/test_published_package.py`, and `tests/test_release_workflows.py`.
They use simulated network/process boundaries and require no live services.
Passing these tests does not establish that account permissions or actual
publication work: complete an explicitly initiated TestPyPI rehearsal after the
[one-time publisher setup](maintainer-basics.md#publisher-setup).

Python distribution is pip/PyPI. Workspace source overrides support development;
the distribution gate uses pip's resolver to test published requirement metadata
with the freshly built packages. `uv.lock` governs development checks, not
consumers' independent installations. Do not rewrite source paths inside CI or
replace wheel verification with editable installs. See
[uv packaging guidance](https://docs.astral.sh/uv/guides/package/) for checking
builds with sources disabled. Containerization is outside this change.
