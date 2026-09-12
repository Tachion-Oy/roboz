# Package maintenance: the practical basics

The main change from application development: other people upgrade your code
without you controlling their environment. Your job is to make that predictable.
This guide describes this repository's setup and a suggested maintenance routine.
The links go to public, human-readable guides, not generated summaries.

## What we ship

One Git repository, four independently versioned **distributions** (pip's unit
of installation). Python's import names are a separate thing:

| Install name | Import name | Responsibility |
| --- | --- | --- |
| `roboz` | `roboz` | Reusable primitives |
| `roboshed` | `roboshed` | Agent factories, workspace, capabilities, and ready-made tools |
| `roboz-endpoints` | `roboz_endpoints` | Model catalogues and optional SDK adapters |
| `roboz-proton-bridge` | `roboz_proton_bridge` | Optional email adapter |

After publication, `pip install roboshed 'roboz-endpoints[openai]'` installs the
shared agent building blocks and the optional OpenAI SDK adapter. An **extra is
a dependency shortcut**, not a separately versioned plugin. Separate distributions
let Proton remain beta without making every package beta. Installation does not
activate an adapter or install the external Proton Bridge application. See [our installation guide](addons.md) and
[PyPA's package terminology](https://packaging.python.org/en/latest/discussions/distribution-package-vs-import-package/).

The future Hub belongs in another repository: backend, UI, configuration and
application-specific behaviour. It consumes these packages like any other user.

## Who uses the files?

There is no literal `_name.py` in this checkout. The likely references are:

- [`src/roboz/__init__.py`](../src/roboz/__init__.py): Python executes this on
  first import of `roboz` in a process. It re-exports the convenient public API,
  so users can write `from roboz import Agent`. It is not a release script.
  `__all__` controls wildcard imports; it does not enforce access restrictions.
- `_something.py`, such as `runtime/_paths.py`: the leading underscore means
  “internal implementation” by convention. Other library modules import it;
  consumers should not rely on it. Python does not prevent them doing so.
- `__name__`: a Python module variable, not a filename.
  `if __name__ == "__main__":` runs a script's entry point when executed
  directly or with `python -m`, rather than when imported.

See the [Python modules tutorial](https://docs.python.org/3/tutorial/modules.html).

Other important files:

- Each `pyproject.toml`: package name, version, dependencies and build settings;
  used by development/build tools and turned into metadata for installers.
- `uv.lock`: exact dependency resolution for developing this workspace; commit
  it. Consumers resolve the published dependency ranges, not this lockfile.
- `README.md`: the front door for users; also supplies the PyPI description.
- `CHANGELOG.md`: the upgrade history for humans; pip does not interpret it.
- `.github/workflows/`: checks and release automation executed by GitHub Actions.

See [uv's explanation of project files](https://docs.astral.sh/uv/concepts/projects/layout/).

## Normal development and the changelog

Work on a branch. Change code, add a regression test, update affected docs,
and add a short entry under `## Unreleased` in the **affected package's**
changelog. Open a pull request, let CI run, review, then merge. A solo maintainer
can use the same routine. No release or version bump is needed for every commit.

The existing changelogs are minimal; use this structure for subsequent changes:

```markdown
## Unreleased

### Fixed

- Preserve concrete nested payload types when passing values between tools.
```

Describe the effect on users, not “refactored core.py”. Use `Added`, `Changed`,
`Fixed`, `Deprecated`, `Removed` or `Security` when relevant; omit empty sections.
Call out breaking changes and show the migration. Purely internal cleanup
usually needs no entry. At release, move these entries under the new version
and actual release date, leaving an empty `Unreleased` section above it.
Do not invent publication dates for the currently staged versions.
See [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Choosing versions

All packages are currently in early development. Publish development snapshots
using `<base>.devN`; do not use alpha, beta, release-candidate, or final versions
until that maturity is explicitly agreed. Existing published versions keep their
historical names. Move each package to development numbering when it is next
published, choosing a base that sorts above its published versions: for example,
Shed moves from `0.1.0a4` to `0.1.1.dev1`, because `0.1.0.dev1` would sort below
`0.1.0a4`. Increment the development number for subsequent snapshots of that base.

Suggested policy: before 1.0, keep patch updates compatible and reserve minor
updates for breaking changes. After 1.0, use major/minor/patch for breaking
changes/features/fixes. This is a promise you adopt; pip cannot check it.
Compatibility includes schemas, persisted data, defaults and side effects—not
only function signatures. See [Semantic Versioning](https://semver.org/).

Python prereleases progress like `0.1.0a1` → `0.1.0b1` → `0.1.0rc1` → `0.1.0`.
These mean alpha, beta, release candidate and final. Installers normally avoid
prereleases unless opted into; explicit prerelease bounds in our extras opt in.
The `proton-bridge-beta` extra name is merely our label, not an enforced channel.
See [PyPA's versioning guide](https://packaging.python.org/en/latest/discussions/versioning/).

Installers determine prerelease status from the version in each package's
`pyproject.toml`; the “Pre-Alpha” classifier is informational. A version such
as `0.1.1` is final, while `0.1.2.dev2` is a development prerelease.
Beta labels are maintainer judgments, not evidence of live-service testing.

Release only affected packages. A Proton fix need not bump core. A core API
break may require companion changes and new dependency bounds. For example,
the current `<0.2.0` bounds intentionally exclude core 0.2.

## Releasing in practice

1. Choose the affected package's version; update its metadata, changelog, and any
   affected dependency bounds. Run `uv lock` and review the lockfile changes.
2. Run the [full validation commands](build-and-test.md), including the installed
   distribution pytest suite against fresh wheels and source archives.
3. Review the release notes and date, then validate the intended tag with
   `uv run python scripts/release_package.py <distribution>-v<version> --check`.
   The checker verifies the tag and declared version; changelog review is manual.
4. Merge the preparation into `main`, tag the reviewed commit with
   `<distribution>-v<version>`, and push that tag. A tag identifies a candidate;
   pushing it does not publish anything.
5. Open **Actions → Release one Python package → Run workflow**. Keep the
   workflow branch set to **main**, enter the existing package tag in **tag**,
   and start the run. This explicitly requests publication after validation.
   Publish required dependency releases first.
6. The workflow resolves the tag to a fixed commit, validates the workspace at
   that commit, and tests the selected wheel with dependencies from PyPI. The
   standard PyPA action then publishes that wheel and source archive without
   rebuilding. If the `pypi` environment requires approval, approve the deployment
   after the verification and artifact-selection jobs pass.

Production publishing currently supports `roboz`, `roboshed`, and
`roboz-endpoints`. Proton Bridge remains part of workspace validation but is not
enabled for publication. Packages remain independently versioned; development,
alpha, and stable releases use the same route. No version bump or upload of
another package is required unless its own changes or dependency bounds require
a release.

The tag must exist and identify a commit already in `main` at the time the
workflow starts. Its version must match that package's metadata at the tagged
commit. Older merged candidates remain valid even if `main` has advanced. Branch
names and raw commit hashes are not release inputs. All checks and builds use the
resolved candidate commit; `main` supplies the workflow definition. There is no
TestPyPI stage or separate validation-only release mode.

### Publisher setup

Each PyPI project grants upload permission separately. Register the same Trusted
Publisher identity on `roboz`, `roboshed`, and `roboz-endpoints`: owner
`Tachion-Oy`, repository `roboz`, workflow `release.yml`, environment `pypi`.
These are three package permissions for one workflow identity. Existing
registrations continue to work with manual releases; no API tokens are needed.
See [PyPI's explanation of one publisher serving many projects](https://docs.pypi.org/trusted-publishers/internals/#why-is-the-pypi-project-to-publisher-relationship-many-many).

In GitHub **Settings → Environments → pypi**, use **Selected branches and tags**
with exactly one rule: **Ref type: Branch**, **Name pattern: main**. Remove the
old package tag rules when migrating to the manual workflow. GitHub evaluates
the workflow's ref for this rule, while the workflow validates the candidate
tag separately. This setting does not protect `main` against changes.

For the current private repository with a sole maintainer, starting the manual
release is the explicit publication action. GitHub plan restrictions currently
prevent configuring required environment reviewers. Before publishing from a
public repository, configure the maintainer as a required reviewer on `pypi`.
The YAML environment name alone does not enforce approval. Configure protection
for `main` and release tags as part of opening the repository to contributors.

To enable another distribution, ensure its path is listed in `PROJECTS`, add its
name to `PUBLISHABLE_PROJECTS` in the release helper, and register this same
publisher identity on its PyPI project. For a new PyPI name, configure a pending
publisher first; establish new projects one at a time. No extra GitHub environment
or deployment pattern is required.

OIDC permission belongs only to the publishing job, which downloads artifacts and
uploads them without checking out or building source. The existing TestPyPI
account configuration is no longer used by this repository's release workflow.
See [PyPA's publishing walkthrough](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

For a bad release, publish a corrected version. Consider **yanking** the bad one:
it discourages new installations without deleting it; exact pins can still
select it. It does not repair existing installations.
See [PyPI's yanking guide](https://docs.pypi.org/project-management/yanking/).

## The small amount of open-source housekeeping

Keep the scope and supported Python/platform versions explicit. Add a short
`CONTRIBUTING.md` describing setup, tests, bug reports and review expectations;
provide a private security-reporting route. Before launch, check rights and
attribution for copied code and review for secrets. This repository already
contains an Apache-2.0 license. See [Starting an Open Source Project](https://opensource.guide/starting-a-project/).

Contributors propose changes through pull requests; maintainers decide what
to merge and support. Set realistic response expectations and decline features
outside the project's scope. Periodically test updated dependencies as well as
the locked environment. See [Best Practices for Maintainers](https://opensource.guide/best-practices/).

Try published packages from a clean consumer project, including controlled live
integration checks where relevant. Consumer applications should use the
independently released dependencies they require.
