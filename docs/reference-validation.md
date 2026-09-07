# Dependency-reference validation

Validated on 2026-09-07 on Linux x86_64.

- Base: `93edea26ebbafc9d8d27e789fc0022fed8593605` (Ctx PR #14 merged).
- Tested implementation: `662ba6b0e84d6febe8b1c099b93caabc61c7ec8c`. Subsequent report-only commits do not change
  shipped source, tests, metadata, or candidate archives.
- Paired Hub implementation: `864f1d4b99c838dd4c523f28191969292ec8545e`.
- Worktree: `/home/tommi/Projects/roboz-worktrees/dependency-reference`; branch `feat/dependency-reference`.
- Default environment: Python 3.13.3, uv 0.6.14. Additional Python 3.14.7
  matrix uses CI-pinned `/tmp/roboz-ci-tools/bin/uv` 0.12.10.
- Fresh candidates: `/tmp/roboz-reference-dist-hr1gc4fl`. No versions, requirements, tags, or
  publication changed. Only core has shipped behavior changes.

## Results

All local required release checks passed. Core/Shed/OpenAI/Proton statement
coverage is **95.69% / 90.76% / 90.00% / 89.72%**, above the respective
95% / 90% / 90% / 89% floors. Both Python versions pass 869 tests.

The regressions exercise first → second → first with distinct dependency IDs
through immutable contexts, bound/copied tools, agent model requests, request
options and OpenRouter policy; deferred construction, client reuse, live
inspection, failed ID/kind resolution retries, transcription references, and
in-flight selection isolation. Static typing checks include custom references
and preserved concrete/lazy helper return types. Paired Hub tests cover exact
checker registration and zero checker calls during inspection.

## Commands

Commands run from the worktree unless a different environment is stated.
Logs are retained locally in `reports/` (ignored by Git).

| Command | Result |
| --- | --- |
| `uv sync --locked --dev` | Pass |
| `uv run pytest tests/unit/llm/test_dependency_references.py --no-cov` | Initially 3 failures from empty test messages; corrected to a real user message, then 6 passed. The later in-flight regression is included in the full 869-test suite. |
| `uv run ruff check src --fix` | Pass |
| `uv run ruff check tests/unit/llm/test_dependency_references.py tests/type_tests/cases/valid/test_endpoint_request_options.py --fix` | Found two misplaced test imports; moved to module top and reran successfully. |
| `uv run ruff format tests/unit/llm/test_dependency_references.py tests/type_tests/cases/valid/test_endpoint_request_options.py` | Pass |
| `uv run pytest --cov=roboz_shed --cov=roboz_openai --cov=roboz_proton_bridge --cov-report=xml:reports/coverage.xml --cov-report=json:reports/coverage.json --junitxml=reports/pytest.xml` | 869 passed; exit 0 |
| `uv run python scripts/check_coverage.py reports/coverage.json` | All four floors pass |
| `uv run pytest tests/e2e --no-cov` | 3 passed |
| `uv run python examples/quickstart.py` | Exit 0 |
| `uv run ruff check` | Exit 0 |
| `uv run pyright` | Exit 0 |
| `bash scripts/run_type_tests.sh` | Exit 0 |
| `uv build --all-packages --out-dir /tmp/roboz-reference-dist-hr1gc4fl` | Exit 0 |
| `uv run twine check /tmp/roboz-reference-dist-hr1gc4fl/*` | Exit 0 |
| `uv run python scripts/check_distributions.py --dist /tmp/roboz-reference-dist-hr1gc4fl` | Exit 0 |
| `git diff --check` | Pass |

Python 3.14 final commands (all pass):

```bash
export PATH=/tmp/roboz-ci-tools/bin:$PATH
export UV_PROJECT_ENVIRONMENT=/tmp/roboz-reference-python3147
uv sync --locked --dev --python 3.14.7
uv run --python 3.14.7 --no-sync pytest --no-cov
uv run --python 3.14.7 --no-sync python examples/quickstart.py
```

Earlier `uv sync --locked --dev --python 3.14` attempts used the installed
3.14.0a6 alpha. With uv 0.6.14, `uv run --no-sync pytest --no-cov` followed
`.python-version` back to 3.13 and could not find pytest (exit 2).
`uv python install 3.14` and `--managed-python` still selected the existing alpha.
With uv 0.12.10 and explicit `uv run --python 3.14 --no-sync pytest --no-cov`,
the alpha exited 139. No application fix or dependency relaxation was applied;
pinning the validation interpreter to stable 3.14.7 resolved this environment
issue. Logs preserve each attempted sync/test separately.

## Candidate archive SHA-256

- `.gitignore`: `684888c0ebb17f374298b65ee2807526c066094c701bcc7ebbe1c1095f494fc1`
- `roboz-0.1.1-py3-none-any.whl`: `15b5cddf54e6f707e02d136f3859f5ad579102b79254d8d646ee35c5de46df25`
- `roboz-0.1.1.tar.gz`: `bb91648d567d23b2a4f951d137b1c27930bbef07281b60b118c731415bb65bf5`
- `roboz_openai-0.1.0a1-py3-none-any.whl`: `c1e3904009895199d81af98889f1439fd301a136f5444083367cfef312d3e60a`
- `roboz_openai-0.1.0a1.tar.gz`: `77f8a06e0c04a0269f009700a0f7747d467ab764a876fec34ab6e85b87db533d`
- `roboz_proton_bridge-0.1.0b1-py3-none-any.whl`: `e7794263f9524b9e4c53cfe9ca6310f490fd1f4c76482a60da72499b7eb9b58e`
- `roboz_proton_bridge-0.1.0b1.tar.gz`: `335dbc6d4fd616313bf3d5b903ca3277639b295ddfc46c0be1bfb3a6587974e2`
- `roboz_shed-0.1.0a1-py3-none-any.whl`: `24b1d21329b9f58292cbecb57d319856aab6471773d56188fa9106ebb9a8cdfa`
- `roboz_shed-0.1.0a1.tar.gz`: `23008dee9a88a1405a55cf57ee89d807fa250b3c4201ce1a5b25553de57daa82`

Independent installed checks pass for wheels and wheels rebuilt from source
archives, including core, companions, combined extras, metadata, import paths,
`pip check`, and deterministic workflows. Local Linux checks do not claim
GitHub Actions or Windows/macOS results. Hub integration evidence is maintained
in its separate `docs/reference-validation.md`.
