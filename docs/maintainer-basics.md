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

After publication, `pip install 'roboz[shed]' 'roboz-endpoints[openai]'` requests extra
dependencies. An **extra is a dependency shortcut**, not a separately versioned
plugin. Separate distributions let Proton remain beta without making every
package beta. Installation does not activate an adapter or install the external
Proton Bridge application. See [our installation guide](addons.md) and
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

1. Choose the affected package's version; update its `pyproject.toml`, changelog
   and any affected dependency bounds. Run `uv lock` and commit the lock changes.
2. Run [the validation commands](build-and-test.md). Build wheels and source
   archives, then test installation outside the checkout using
   `scripts/check_distributions.py`. Passing source tests alone does not prove
   the installed package works.
3. Review and merge the release preparation. Validate the intended tag with
   `uv run python scripts/release_package.py roboz-proton-bridge-v0.1.0b2 --check`
   (example: requires that package's version to be `0.1.0b2`, with dated release
   notes and an empty Unreleased section). Also run `uv lock --check`.
4. Tag the reviewed commit with `<distribution>-v<version>` and push that tag.
   The [release workflow](../.github/workflows/release.yml) first checks preparation
   and version availability, then validates the whole workspace. It publishes
   only the tagged package to TestPyPI and checks installation with real PyPI
   dependencies. If successful, it waits at the configured production approval.
5. Review the verified package/version and choose **Approve and deploy** in the
   GitHub run's **Review deployments** dialog. Production uses the same verified
   archives, without rebuilding. The workflow checks a fresh download and
   installation from PyPI afterward; a failed post-publication check reports a
   problem but cannot undo an upload. Optionally create a GitHub Release
   using the same changelog notes. A Git tag identifies source; a GitHub Release
   presents notes; a PyPI release holds installable artifacts. They are distinct.

A wheel (`.whl`) is the built installation artifact; a source distribution
(`.tar.gz`) allows building from source. See [PyPA's packaging flow](https://packaging.python.org/en/latest/flow/).

Use the manual TestPyPI route to exercise uploading and installing before the
first production release. Choose one package in **Actions → Release one Python
package → Run workflow**; manual runs end after TestPyPI checks and cannot publish
to production. See [the rehearsal guide](build-and-test.md#release-preparation-and-testpypi-rehearsals)
for version preparation, dependency ordering, and retry behavior.

### Publisher setup

TestPyPI and PyPI have separate accounts, projects, and Trusted Publisher settings.
Establish ownership for each distribution being published on the selected index.
For new projects that share the same publisher configuration, register one pending
publisher, publish that package, then register the next. The index allows only
one pending project per publisher identity; completed projects can share it.
Configure each publisher for owner
`Tachion-Oy`, repository `roboz`, workflow `release.yml`, and the matching GitHub
environment: `testpypi` for TestPyPI, `pypi` for production.

In GitHub **Settings → Environments**, create those environments. Configure
`pypi` with the maintainer as a required reviewer; leave self-approval available
if that maintainer also initiates releases. Restrict production deployments to
the four package release tag patterns and protect those tags with repository
rulesets. TestPyPI does not need a second approval after manually starting a
rehearsal. Protect any branches permitted to publish to TestPyPI as appropriate.

**The environment name in YAML does not enforce approval by itself.** Required
reviewers must be configured in GitHub before enabling production publishing.
If the repository's visibility/plan does not support required reviewers, keep
production publishing disabled until an explicit approval mechanism is available.
These account settings cannot be established by local repository checks.

The workflow gives OIDC permission only to the two publishing jobs; those jobs
download artifacts and upload them without checking out or building source.
Configure production trust only after a TestPyPI rehearsal succeeds. These
workflow changes themselves create no tags and publish no packages.
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

**Next step:** try the built packages from a clean consumer project, including
controlled live model/email checks. Then settle the public compatibility policy
and publish the initial packages: core first, Shed/model adapter next, Proton
last. Build the Hub against those installed dependencies.
