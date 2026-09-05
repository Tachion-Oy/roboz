# Source-history port audit

## Optional package extraction

The companion slice ports file commands, patch editing, guards, email tools,
Proton Bridge, and their associated skills from PeffaShed
`2c701486acd77baeaf80d6834d1f52d2cf08d5b3`. Its 246 selected regression cases
are retained under the companion test directories. The SDK adapter uses explicit
model configuration rather than porting the all-provider catalog.

Shared guards now use generic payloads; their in-memory tool handoff requires
core 0.1.1. Old email request-id headers remain compatible. The new environment
prefix is `ROBOZ_PROTON_BRIDGE_`; signatures are caller-supplied.

The exclusions below describe the core distribution. Companion packages now
provide the scoped optional integrations; see [add-ons](addons.md). Codex,
document backends, arbitrary shell/Git workflows, and Hub remain outside this
slice. Automated coverage uses mock endpoints and IMAP; live account smoke tests
remain user-run checks, with no live-service validation claimed by this ledger.

Audit date: 2026-09-04

This ledger records the post-`stable-2026-08-31` source changes reviewed while
building branch `port/parity-2026-09-04`. It distinguishes reusable Roboz
behavior from PeffaHub application and UI behavior so omissions are explicit.

## Audited ranges

| Repository | Stable commit | Audited head |
| --- | --- | --- |
| Peffa | `4e9a997` | `f2bec32` |
| PeffaShed | `948da63` | `2c70148` |
| PeffaHub | `74bc521` | `da71c1f` |

Every non-merge commit in these ranges was inspected. Merge commits were also
checked for merge-only resolutions; none added an independent behavior change.
Unmerged `backup/*`, abandoned feature, and UI experiment branches were inspected
but are not parity targets for the three audited default branches.

## Peffa

| Source commit | Behavior | Roboz commit |
| --- | --- | --- |
| `ea750cf` | Response diagnostics | `2b301b8` |
| `545f3aa`, `3ffe511` | Logging cleanup and simplification | `72659f4`, `2b301b8` |
| `b58918b` | Separate runtime logging from event emission | `72659f4`, `2b301b8` |
| `b807344` | Input/output/reasoning token breakdown | `2b301b8` |
| `a73d56d` | Per-endpoint request options | `2b301b8` |

All 19 regression-test function names added by this Peffa range have Roboz
counterparts.

## PeffaShed

| Source commit | Behavior | Roboz commit |
| --- | --- | --- |
| `6a00769` | Authority/recency reconciliation and gradual memory decay | `935e26a` |
| `c339b39` | Three bounded attempts with 15% length tolerance | `935e26a`, `3f046f3` |
| `fd41d8b` | Prune empty snapshot directories | `c15bdb4`, `3f046f3` |
| `422c4ca`, `9689750` | Structured logging without pipe/log duplication | `72659f4`, `935e26a`, `9a6fa52`, `1f75df8` |
| `ab0e570` | Preserve the complete source conversation with `NO_TRUNCATION` | `935e26a` |
| `ddba47d` | Ensure provider secrets do not enter Librarian persistence | `3f046f3` |
| `ab0b8b4` | Canonical model IDs; request policy belongs at composition | `0df1307` |

The port includes the dependency-free substrate required to make those deltas
real: timestamped snapshot storage, chronological normalization, terminal modes,
provenance, activity-aware consolidation, concurrent-writer suppression,
retention, cancellable waits, and the deterministic Librarian composition.

## PeffaHub

| Source commit | Disposition |
| --- | --- |
| `7fc6b9a` | Ported in `0df1307`: canonical OpenRouter identity, throughput routing, required-parameter routing, optional provider exclusions, and per-use reasoning effort. |
| `cd234d5` | Superseded by `615024d`. The shared millisecond formatter already exists in Roboz. |
| `615024d` | Application-layer logger selection, rotating JSONL file policy, HTTP access filtering, and Hub lifecycle messages remain PeffaHub concerns. Roboz provides the reusable `LOG_FORMAT`, `LOG_DATE_FORMAT`, and `log_with_data` primitives they consume. |
| `ca1bc3d`, `d51f8bc`, `ebaf526`, `188eea6`, `1dedef8`, `d8d2471`, `da71c1f` | Hub API/UI behavior; outside this library distribution. |

Roboz deliberately does not add OpenAI, Cerebras, Groq, FastAPI, or UI
dependencies. Provider catalogs and application wiring remain companion-layer
code; the typed, dependency-free request policy and runtime contracts live here.

## Installed successor contracts

The release-quality CI additions identify `tests/e2e/` as the Linux workflow
layer for retained PeffaShed behavior: guarded read/edit/read, filesystem escape
denial, subagent completion, and conversation → snapshot → memory → retention.
The archive installation gate executes the same scenarios from original wheels
and source-archive rebuilds, outside this checkout.

PeffaHub's successor RoboSprawl owns browser and HTTP application behavior.
Roboz tests the pinned successor's composition and HTTP run/reply/completion
contract with candidate library wheels. RoboSprawl's own required Chromium,
Firefox, and WebKit jobs cover the inherited UI journeys and backend restart
recovery. This extends verification of retained behavior; it does not port the
previously excluded integrations or claim credential-backed service parity.
