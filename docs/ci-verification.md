# CI implementation verification — 2026-09-05

These are local Linux results, not GitHub Actions results. Tooling: Python
3.13.3 and stable 3.14.7, uv 0.12.10, and actionlint 1.7.12. All four Roboz
distributions are affected by validation changes; no library runtime API,
prompt, persisted format, dependency range, or version was changed.

| Command/check | Local result |
| --- | --- |
| `uv sync --locked --dev` | Passed with CI-pinned uv; separate environments for both Python versions |
| `uv run pytest` with all four coverage sources and JUnit/JSON reports | Passed after the review corrections: 770 passed on Python 3.13; the earlier review run passed 770 on Python 3.14 |
| `uv run python scripts/check_coverage.py <coverage.json>` | Passed after the corrections on Python 3.13 and in the earlier Python 3.14 run: core 95.27%, Shed 90.34%, OpenAI 90.00%, Proton Bridge 89.72% |
| `uv run python examples/quickstart.py` | Passed on both versions |
| `uv run ruff check` | Passed |
| `uv run pyright` | Passed, zero diagnostics |
| `bash scripts/run_type_tests.sh` | Positive cases passed; all 14 negative cases failed as expected |
| `uv build --no-sources --all-packages --out-dir <fresh-dir>` | Built eight candidate archives |
| `uv run twine check <fresh-dir>/*` | All eight passed |
| `uv run python scripts/check_distributions.py --dist <fresh-dir>` | Passed after the corrections for independent core, Shed, OpenAI, Proton, and extras pip environments using original wheels and source-archive rebuilds; installed E2E and demo conversation persistence passed |
| `ROBOSPRAWL_ROOT=<sentinel> uv run python scripts/check_downstream.py --source <pinned-checkout> --dist <fresh-dir>` | Passed after the corrections against RoboSprawl `7f956cb6cfd93db0cc1f95c7dcffc9eed0e7e8b5`: composition and HTTP create/stream/reply/completion passed, and the inherited sentinel directory remained untouched |
| `uv run python scripts/release_package.py roboz-v0.1.1 --check` | Passed; no tag or publication performed |
| `uv run pytest tests/test_release_package.py tests/test_coverage_gate.py tests/e2e --no-cov` | 15 passed after the corrections, including installed workflow contracts, byte-preserving selection, incomplete archives, occupied output, separate coverage floors, and missing coverage |
| `actionlint .github/workflows/*.yml` | Passed, including reusable validation and aggregate status wiring |
| `bash -n scripts/run_type_tests.sh` | Passed |
| `git diff --check` | Passed |

The post-correction candidate directory was `/tmp/roboz-candidates.Ld1sQN`;
current Python 3.13 test and coverage reports are under the ignored `reports/`
directory. Earlier review logs and Python 3.14 reports remain temporary local
evidence. CI retains corresponding artifacts for 14 days.

Documentation updated: build/test guide, contributor guide, maintainer release
instructions, and the source-history parity ledger. No Roboz changelog entry is
needed for these repository-maintenance and test-only changes. RoboSprawl's
separate changelog records its source-archive and E2E-runner changes.

Not locally verified: the actual GitHub Actions runs, Windows/macOS core wheel
jobs, and publication/trusted-publisher permissions. Those platform jobs are
configured as required gates and must pass in CI before claiming the complete
platform acceptance matrix. Containerization and publishing remain outside scope.

An anonymous GitHub API check of both `Tachion-Oy/roboz` and
`Tachion-Oy/robosprawl` returned HTTP 404. This cannot distinguish private from
unpublished/missing repositories, but it does mean public access was not
verified. Cross-repository checkout jobs require those pinned repositories to
be publicly accessible for credential-free fork PRs. No secret fallback, skipped
gate, repository visibility change, push, tag, or publication was introduced.
