# Testing Practices

This repository values focused tests that prove behavior without making the suite
larger or more brittle than the code being tested. Tests should make future
changes safer, not harder.

## Default Standard

- Test public behavior and stable contracts. Avoid asserting incidental call
  order, private helper details, or exact implementation structure unless that is
  the behavior being protected.
- Keep each test small enough to explain the scenario in its name and setup. If
  a test needs a long script of unrelated setup, split it or move the repeated
  part behind a clear fixture/helper.
- Prefer one meaningful assertion group per scenario. Do not create broad
  catch-all tests that fail for several unrelated reasons.
- Add regression tests for the bug or contract being changed. Do not expand a
  narrow fix into a large speculative matrix.
- Use parametrization when it removes repetition while keeping each case easy to
  read. Do not create large parameter grids unless the combinations represent
  real supported behavior.

## Mocking and Monkeypatching

Monkeypatching is allowed only when it isolates a true boundary that the test
does not own, such as the filesystem root, environment variables, time, process
execution, or an external service. It should not be the default way to make code
testable.

- Prefer dependency injection over monkeypatching. For agents and tools, pass a
  scripted endpoint, fake sink, temp path, or explicit collaborator when the API
  supports it.
- Patch at the narrowest stable boundary. Patch the module function that performs
  the external effect, not a chain of internal implementation details.
- Keep patches local to the test using `monkeypatch`, `patch`, or fixtures with
  explicit scope. Do not leak patched global state between tests.
- Do not monkeypatch large portions of the system just to force a branch. If the
  test requires many patches, the production code probably needs a clearer
  dependency boundary or the test should move up a level.
- Do not mock the behavior under test. A mock may replace an external dependency,
  but the code path being verified should be real.

Good examples in this codebase include using a scripted LLM endpoint instead of
calling a provider, and using `tmp_path` for persisted files instead of writing
to a user data directory.

## Test Size and Coverage

More tests are not automatically better. The suite should cover important
behavior with the smallest clear set of cases.

- Cover normal behavior, boundary/error behavior, and regressions that have
  actually mattered.
- Avoid duplicating the same assertion through several tests with different
  setup noise.
- Prefer a representative case plus a targeted edge case over exhaustive
  permutations.
- Keep fixture graphs shallow. A reader should not need to jump through several
  files to understand basic setup.
- Avoid snapshot-style assertions over large outputs unless the output itself is
  the contract. Prefer precise assertions over relevant fields.

## Agent-Specific Guidance

- For agent behavior, drive `Agent` through its public methods and inject a
  deterministic endpoint. Assert final outputs, selected tools, emitted events,
  or persisted conversation data.
- For tools, test the callable contract: input model, output model, errors, and
  side effects that are part of the tool's promise.
- For runtime/event code, assert emitted event shape and ordering only where the
  order is contractual.
- For primitive type behavior, add cases under `tests/primitives/type_tests/`.
  Keep provider-specific cases under `tests/type_tests/` instead of encoding
  type-checker expectations into runtime tests.
- Keep tests under `tests/unit/` fast and deterministic. Anything requiring real
  network, provider credentials, or long-running processes does not belong in
  the default pytest path.

## Review Checklist

Before adding or accepting tests, check that:

- The test fails for the bug or contract it claims to protect.
- The setup is as small as the scenario allows.
- Mocks and monkeypatches replace only external boundaries.
- The production behavior under test is real, not mocked away.
- The number of cases is justified by distinct behavior, not by bulk.
- The test can be understood without knowing hidden fixture side effects.
