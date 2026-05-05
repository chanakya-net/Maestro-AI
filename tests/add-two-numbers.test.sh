#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="${ROOT_DIR}/add-two-numbers.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  if [[ "${expected}" != "${actual}" ]]; then
    fail "${message} (expected: ${expected}, actual: ${actual})"
  fi
}

output="$("${SCRIPT_PATH}" 1.5 2)"
assert_equals "3.5" "${output}" "adds two numeric inputs"

set +e
invalid_output="$("${SCRIPT_PATH}" two 2 2>&1)"
invalid_status=$?
set -e

assert_equals "1" "${invalid_status}" "rejects non-numeric input with a non-zero exit"
assert_equals "error: both inputs must be numeric" "${invalid_output}" "rejects non-numeric input with a clear error"

set +e
missing_output="$("${SCRIPT_PATH}" 1 2>&1)"
missing_status=$?
set -e

assert_equals "1" "${missing_status}" "rejects missing input with a non-zero exit"
assert_equals "error: expected exactly two numeric arguments" "${missing_output}" "rejects missing input with a clear error"

echo "PASS: adds two numeric inputs"
echo "PASS: rejects invalid input"
echo "PASS: rejects missing input"
