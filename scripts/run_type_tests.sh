#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALID_DIR="$ROOT_DIR/tests/type_tests/cases/valid"
EXPECTED_FAILURE_DIR="$ROOT_DIR/tests/type_tests/cases/expected_failures"
CONFIG="$ROOT_DIR/pyrightconfig.type-tests.json"
VERBOSE="${TYPE_TESTS_VERBOSE:-0}"

echo "== Valid Type Tests =="
valid_output=""
if ! valid_output="$(uv run pyright --project "$CONFIG" "$VALID_DIR" 2>&1)"; then
  echo "UNEXPECTED_FAIL  valid/"
  if [[ "$VERBOSE" == "1" ]]; then
    echo "$valid_output"
  else
    echo "Set TYPE_TESTS_VERBOSE=1 to print full pyright diagnostics."
  fi
  exit 1
fi
echo "PASS            valid/"

shopt -s nullglob
expected_failure_files=("$EXPECTED_FAILURE_DIR"/*.py)
shopt -u nullglob
[[ ${#expected_failure_files[@]} -gt 0 ]] || {
  echo "No expected-failure type tests found in $EXPECTED_FAILURE_DIR"
  exit 1
}

echo
echo "== Expected-Failure Type Tests =="
unexpected_passes=()
unexpected_fails=()

for file in "${expected_failure_files[@]}"; do
  name="$(basename "$file")"
  output=""
  if output="$(uv run pyright --project "$CONFIG" "$file" 2>&1)"; then
    echo "UNEXPECTED_PASS  $name"
    unexpected_passes+=("$name")
    if [[ "$VERBOSE" == "1" ]]; then
      echo "$output"
    fi
  else
    # Expected path: file should fail type-checking.
    echo "PASS_EXPECTED_FAIL  $name"
    # Guard against tooling/runtime issues that don't produce type diagnostics.
    if [[ "$output" != *"error:"* ]]; then
      unexpected_fails+=("$name")
      echo "UNEXPECTED_FAIL  $name (pyright failed without diagnostics)"
      if [[ "$VERBOSE" == "1" ]]; then
        echo "$output"
      fi
    fi
  fi
done

echo
echo "== Summary =="
echo "unexpected_passes: ${#unexpected_passes[@]}"
echo "unexpected_fails:  ${#unexpected_fails[@]}"

if [[ ${#unexpected_passes[@]} -gt 0 ]]; then
  echo "files_with_unexpected_pass:"
  for name in "${unexpected_passes[@]}"; do
    echo "  - $name"
  done
fi

if [[ ${#unexpected_fails[@]} -gt 0 ]]; then
  echo "files_with_unexpected_fail:"
  for name in "${unexpected_fails[@]}"; do
    echo "  - $name"
  done
fi

if [[ ${#unexpected_passes[@]} -gt 0 || ${#unexpected_fails[@]} -gt 0 ]]; then
  echo
  echo "Type tests failed."
  exit 1
fi

echo
echo "Type tests passed."
