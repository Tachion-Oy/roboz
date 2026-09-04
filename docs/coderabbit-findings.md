# CodeRabbit Finding Triage

This document records the validation of the CodeRabbit findings on PR #2
(`feat/port-primitives`). The findings were checked against the implementation and
against how the corresponding Peffa and PeffaShed components are used by PeffaHub.

The smaller primitives-first port should not grow to include unrelated standard
tools, persistence, provider, or summarization work. Findings outside that port's
scope should remain follow-up issues until their modules are ported.

## Smaller port disposition

The primitives-only port handled the non-object completion JSON validation, the
public `bind_api_user_io` error wording, the subagent test filename, and the
invalid-sequence test comment. The standalone layout correctly uses `tests/unit`,
so the earlier path finding no longer applies. Shell-script and compactification
test cleanups remain deferred because those add-on modules are not included.

The later Peffa parity port resolved finding 13 at Roboz's built-in diagnostic
call sites by replacing raw provider diagnostics with DEBUG-level, scalar
structured metadata. Those call sites do not log provider bodies, exception
strings, or tracebacks; the public `log_with_data` helper intentionally leaves
caller-provided logging policy to its caller.

The parity port also resolved finding 2 with a versioned message-sequence cursor
stored in each new snapshot. Artifact timestamps remain file-ordering metadata;
they are only a compatibility fallback for snapshots written before the cursor
was introduced. Invalid cursors and naive or invalid legacy timestamps are
reprocessed conservatively instead of risking silent message loss.

## Handle in the smaller PR only if included

- Validate decoded LLM JSON is an object before model construction.
- Correct the stale `tests/unit/` documentation path.
- Correct the API I/O error to reference `bind_api_user_io`.
- Fix relevant weak or vacuous tests included in the smaller port:
  - Rename `test_subprocess.py` to reflect that it tests the subagent wrapper.
  - Strengthen the shell exit-code assertion to check `exit 3` rather than `3`.
  - Remove or correct the unused `tmp_path` assertion in the compactification test.
  - Correct the inaccurate invalid-sequence test comment.

## Follow-up issues

### 1. Harden LLM and message JSON object boundaries

- Validate that completion JSON is an object before constructing output models.
- Handle non-object JSON in `remind_agent` without raising `TypeError`.

### 3. Constrain persisted conversation identifiers

- Validate marker `conversation_id` values.
- Prevent persisted conversation IDs from escaping `snapshot_root`.

Normal Hub activity markers use internally generated UUIDs. Snapshot input is less
trusted because conversation data is loaded from persisted JSON.

### 4. Close the `rg --pre=` command-execution bypass

- Reject `--pre=...` and `--pre-glob=...`, not only the separate-token forms.
- Add regression tests for both forms.

This must be fixed before the CLI-command skill is released. The bypass was
reproduced during review.

### 5. Correct grep and ripgrep operand extraction

- Support repeated `-e` pattern arguments.
- Support `-f` pattern-file arguments.
- Ensure pattern values are never interpreted and rewritten as path operands.

### 6. Propagate cancellation and deadlines into compactification

- Pass the agent pipe into conversation summarization.
- Add an explicit provider timeout.

PeffaHub's injected `KeyboardInterrupt` makes its caller responsive, but the
abandoned provider worker can continue and retain an external-call slot.

### 7. Review EventPipe cross-thread state

- Protect sequence, message ID, and chunk-index state.
- Define behavior for late streaming callbacks after abandonment.

PeffaHub normalizes frontend event sequences under its own lock, which reduces the
current impact but does not make the generic EventPipe state thread-safe.

### 8. Make overwrite confirmation semantics consistent

- Apply DELETE confirmation rules when CREATE overwrites an existing file.

The current Hub policy asks for CREATE and DELETE together, so this is a generic
policy correctness issue rather than a current Hub bypass.

### 9. Harden provider catalog attribute names

- Handle Python keywords and names beginning with digits.
- Reject or escape reserved catalog attributes and private-looking names.

The current Hub catalog is curated and does not contain affected model IDs.

### 10. Improve runtime portability and path normalization

- Replace or document Windows-incompatible stdin timeout handling.
- Resolve `scripts_dir` when constructing the shell tool.
- Use explicit UTF-8 for persisted artifacts.
- Return a model-facing refusal when `bash` is unavailable.

PeffaHub uses API I/O and passes an absolute sandbox scripts directory, so the first
two items do not currently affect it.

### 12. Clarify dry-run event semantics

- Decide whether complete messages and script output should be suppressed.
- Apply the decision consistently across all EventPipe emitters.

### 14. Minor cleanup bundle

- Make `[None]` skill-prompt input return an empty prompt or narrow its annotation.
- Replace the stale `rbz_solutions` example with deployment-neutral wording.
- Apply the documentation and test cleanups listed above when their modules land.

## Dismissed findings

- **Reset background-agent state after a start timeout.** The thread may still be
  alive; clearing its state could spawn a duplicate daemon.
- **Recycle slots belonging to abandoned provider workers.** Retaining the slot
  until the worker exits intentionally bounds leaked provider threads. Releasing it
  early would allow unbounded orphan workers.
- **Validate the Hub project slug inside `Project`.** PeffaHub already normalizes all
  project names upstream, and the library assigns folder naming policy to callers.
- **Emit a final shell `MessageEvent` during cancellation.** PeffaHub explicitly
  closes an in-flight stream on lifecycle `stopped`, and cancellation finalizes the
  lifecycle.
- **Give every factory materialization a new ID.** Stable factory identity is
  required so chain targets can refer to a factory before context materialization.
  `Tool.copy()` creates a distinct logical tool when needed.
- **Add special handling for a missing initial-message Markdown file.** PeffaHub
  passes a directory, and direct missing-file errors already identify the path.
- **Handle `stopped` lifecycle events with `status=None` in persistence.**
  `EventPipe.finalize_run` requires a `RunStatus` and cannot produce that event.
- **Expand arbitrary dependency sequences.** Tuple expansion is intentional and
  tested; arbitrary mutable sequences conflict with the frozen factory-context
  contract.
- **Add multi-Librarian artifact locking and concurrent-removal recovery.** Each
  orchestrator/project has exactly one Librarian, its maintenance stages execute
  sequentially, and it exclusively owns its snapshot, memory, and retention
  artifacts. Cross-process mutation is outside that ownership contract.

## Validation summary

- 36 CodeRabbit findings reviewed.
- 16 confirmed.
- 12 technically valid but downgraded because they are defensive, portability-only,
  or do not affect PeffaHub's actual integration.
- 8 rejected.
- Of the 19 major or critical inline findings, 8 were confirmed, 6 were downgraded,
  and 5 were rejected.
