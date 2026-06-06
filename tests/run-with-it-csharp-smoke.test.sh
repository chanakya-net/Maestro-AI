#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

# Check if dotnet SDK is available
if ! command -v dotnet >/dev/null 2>&1; then
  echo "SKIP: dotnet SDK is not available"
  exit 0
fi

# Check if dotnet SDK version is 10+
DOTNET_VERSION="$(dotnet --version)"
MAJOR_VERSION="$(echo "${DOTNET_VERSION}" | cut -d. -f1)"
if [[ "${MAJOR_VERSION}" -lt 10 ]]; then
  echo "SKIP: dotnet SDK version is ${DOTNET_VERSION}, but 10+ is required"
  exit 0
fi

echo "Running C# helper smoketests using .NET SDK version ${DOTNET_VERSION}..."

# 1. run-with-it-state.cs
set +e
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-state.cs" -- --help >/dev/null 2>&1
status_state_help=$?
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-state.cs" >/dev/null 2>&1
status_state_empty=$?
set -e
assert_equals "0" "${status_state_help}" "run-with-it-state.cs --help exit code"
assert_equals "2" "${status_state_empty}" "run-with-it-state.cs empty args exit code"
echo "PASS: run-with-it-state.cs smoketests"

# 2. run-with-it-router.cs
set +e
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" -- --help >/dev/null 2>&1
status_router_help=$?
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" >/dev/null 2>&1
status_router_empty=$?
set -e
assert_equals "0" "${status_router_help}" "run-with-it-router.cs --help exit code"
assert_equals "2" "${status_router_empty}" "run-with-it-router.cs empty args exit code"
echo "PASS: run-with-it-router.cs smoketests"

# 3. run-with-it-artifacts.cs
set +e
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-artifacts.cs" -- --help >/dev/null 2>&1
status_artifacts_help=$?
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-artifacts.cs" >/dev/null 2>&1
status_artifacts_empty=$?
set -e
assert_equals "0" "${status_artifacts_help}" "run-with-it-artifacts.cs --help exit code"
assert_equals "2" "${status_artifacts_empty}" "run-with-it-artifacts.cs empty args exit code"
echo "PASS: run-with-it-artifacts.cs smoketests"

# 4. run-with-it-github-update.cs
set +e
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-github-update.cs" -- --help >/dev/null 2>&1
status_github_help=$?
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-github-update.cs" >/dev/null 2>&1
status_github_empty=$?
set -e
assert_equals "0" "${status_github_help}" "run-with-it-github-update.cs --help exit code"
assert_equals "2" "${status_github_empty}" "run-with-it-github-update.cs empty args exit code"
echo "PASS: run-with-it-github-update.cs smoketests"

# 5. run-with-it-pr-body.cs
set +e
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-pr-body.cs" -- --help >/dev/null 2>&1
status_pr_help=$?
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-pr-body.cs" >/dev/null 2>&1
status_pr_empty=$?
set -e
assert_equals "0" "${status_pr_help}" "run-with-it-pr-body.cs --help exit code"
assert_equals "2" "${status_pr_empty}" "run-with-it-pr-body.cs empty args exit code"
echo "PASS: run-with-it-pr-body.cs smoketests"

# 6. Minimal deterministic command for router helper
echo "Running minimal deterministic command on run-with-it-router.cs..."
router_output="$(dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" -- \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${ROOT_DIR}/assets/non-existent-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude)"

if [[ ! "${router_output}" =~ "codex" ]] || [[ ! "${router_output}" =~ "gpt-5.4-mini" ]]; then
  fail "C# router minimal command failed to select expected agent/model (output was: ${router_output})"
fi
echo "PASS: run-with-it-router.cs minimal deterministic command"

echo "ALL C# SMOKE TESTS PASSED"
