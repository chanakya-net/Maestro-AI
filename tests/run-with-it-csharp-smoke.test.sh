#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

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

compare_text_files() {
  local expected_file="$1"
  local actual_file="$2"
  local message="$3"

  if ! cmp -s "${expected_file}" "${actual_file}"; then
    echo "Expected:" >&2
    cat "${expected_file}" >&2
    echo "Actual:" >&2
    cat "${actual_file}" >&2
    fail "${message}"
  fi
}

compare_json_projection() {
  local expected_file="$1"
  local actual_file="$2"
  local projection="$3"
  local message="$4"

  python3 - "$expected_file" "$actual_file" "$projection" "$message" <<'PY'
import json
import sys
from pprint import pformat

expected_file, actual_file, projection, message = sys.argv[1:5]


def load(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def decision_without_timestamp(item):
    return {key: value for key, value in item.items() if key != "selected_at"}


def project(kind: str, payload):
    if kind == "state_mark":
        issue = payload["issue_registry"]["136"]
        return {
            "status": issue["status"],
            "context_file": issue["context_file"],
            "issue_dir": issue["issue_dir"],
            "pid": issue["pid"],
            "log_file": issue["log_file"],
            "done_file": issue["done_file"],
            "report_file": issue["report_file"],
            "active_pool_issues": payload["active_pool_issues"],
        }

    if kind == "state_finalize":
        issue = payload["issue_registry"]["136"]
        return {
            "status": issue["status"],
            "active_pool_issues": payload["active_pool_issues"],
            "completed_summaries": payload["completed_summaries"],
            "ledger_rows": payload["ledger_rows"],
        }

    if kind == "router_record_output":
        return {
            "agent": payload["agent"],
            "model": payload["model"],
            "role": payload["role"],
            "complexity_level": payload["complexity_level"],
            "routing_level": payload["routing_level"],
            "selection_reason": payload["selection_reason"],
            "ledger": {
                "updated": payload["ledger"]["updated"],
                "total_decisions": payload["ledger"]["total_decisions"],
                "agent_counts": payload["ledger"]["agent_counts"],
                "selected_agent_count": payload["ledger"]["selected_agent_count"],
            },
            "evaluated_candidates": payload["evaluated_candidates"],
        }

    if kind == "router_record_ledger":
        return {
            "schema_version": payload["schema_version"],
            "decisions": [decision_without_timestamp(item) for item in payload["decisions"]],
            "totals": payload.get("totals", {}),
        }

    if kind == "router_review_output":
        return {
            "agent": payload["agent"],
            "model": payload["model"],
            "role": payload["role"],
            "complexity_level": payload["complexity_level"],
            "routing_level": payload["routing_level"],
            "selection_reason": payload["selection_reason"],
            "evaluated_candidates": payload["evaluated_candidates"],
        }

    if kind == "github_update_state":
        issue = payload["issue_registry"]["136"]
        return {
            "github_update_status": issue["github_update_status"],
            "github_update_detail": issue["github_update_detail"],
        }

    raise SystemExit(f"unknown projection: {kind}")


expected = project(projection, load(expected_file))
actual = project(projection, load(actual_file))
if expected != actual:
    print(f"{message}\nExpected:\n{pformat(expected)}\nActual:\n{pformat(actual)}", file=sys.stderr)
    sys.exit(1)
PY
}

check_usage_contract() {
  local helper="$1"
  local path="${ROOT_DIR}/assets/csharp/${helper}.cs"

  set +e
  dotnet run "${path}" -- --help >/dev/null 2>&1
  local help_status=$?
  dotnet run "${path}" >/dev/null 2>&1
  local empty_status=$?
  set -e

  assert_equals "0" "${help_status}" "${helper}.cs --help exit code"
  assert_equals "2" "${empty_status}" "${helper}.cs empty args exit code"
  echo "PASS: ${helper}.cs usage contract"
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

for helper in \
  run-with-it-state \
  run-with-it-router \
  run-with-it-artifacts \
  run-with-it-github-update \
  run-with-it-pr-body
do
  check_usage_contract "${helper}"
done

STATE_PY="${TMP_ROOT}/state-py.json"
STATE_CS="${TMP_ROOT}/state-cs.json"
STATE_REPORT="${TMP_ROOT}/state-report.json"
COMMON_CONTEXT="${TMP_ROOT}/context.md"
COMMON_ISSUE_DIR="${TMP_ROOT}/issue"
COMMON_LOG="${TMP_ROOT}/worker.log"
COMMON_DONE="${TMP_ROOT}/worker.done"

cat > "${STATE_PY}" <<'JSON'
{"issue_registry":{},"active_pool_issues":[]}
JSON
cp "${STATE_PY}" "${STATE_CS}"

python3 "${ROOT_DIR}/assets/python/run-with-it-state.py" mark-in-progress \
  --state-file "${STATE_PY}" \
  --issue 136 \
  --context-file "${COMMON_CONTEXT}" \
  --issue-dir "${COMMON_ISSUE_DIR}" \
  --pid 123 \
  --log-file "${COMMON_LOG}" \
  --done-file "${COMMON_DONE}" \
  --report-file "${STATE_REPORT}" >/dev/null

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-state.cs" -- mark-in-progress \
  --state-file "${STATE_CS}" \
  --issue 136 \
  --context-file "${COMMON_CONTEXT}" \
  --issue-dir "${COMMON_ISSUE_DIR}" \
  --pid 123 \
  --log-file "${COMMON_LOG}" \
  --done-file "${COMMON_DONE}" \
  --report-file "${STATE_REPORT}" >/dev/null

compare_json_projection "${STATE_PY}" "${STATE_CS}" "state_mark" "state mark-in-progress parity mismatch"
echo "PASS: run-with-it-state.cs mark-in-progress parity"

cat > "${STATE_REPORT}" <<'JSON'
{
  "outcome": "completed",
  "summary": "ok",
  "verification": {
    "passed": true
  },
  "review_cycles": 2,
  "commit_sha": "abc123",
  "model_usage": []
}
JSON

python3 "${ROOT_DIR}/assets/python/run-with-it-state.py" finalize-issue \
  --state-file "${STATE_PY}" \
  --issue 136 \
  --report-file "${STATE_REPORT}" >/dev/null

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-state.cs" -- finalize-issue \
  --state-file "${STATE_CS}" \
  --issue 136 \
  --report-file "${STATE_REPORT}" >/dev/null

compare_json_projection "${STATE_PY}" "${STATE_CS}" "state_finalize" "state finalize-issue parity mismatch"
echo "PASS: run-with-it-state.cs finalize-issue parity"

ROUTER_RECORD_PY_LEDGER="${TMP_ROOT}/router-record-py.json"
ROUTER_RECORD_CS_LEDGER="${TMP_ROOT}/router-record-cs.json"
ROUTER_RECORD_PY_OUTPUT="${TMP_ROOT}/router-record-py-output.json"
ROUTER_RECORD_CS_OUTPUT="${TMP_ROOT}/router-record-cs-output.json"

python3 "${ROOT_DIR}/assets/python/run-with-it-router.py" \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${ROUTER_RECORD_PY_LEDGER}" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude \
  --record > "${ROUTER_RECORD_PY_OUTPUT}"

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" -- \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${ROUTER_RECORD_CS_LEDGER}" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude \
  --record > "${ROUTER_RECORD_CS_OUTPUT}"

compare_json_projection "${ROUTER_RECORD_PY_OUTPUT}" "${ROUTER_RECORD_CS_OUTPUT}" "router_record_output" "router --record output parity mismatch"
compare_json_projection "${ROUTER_RECORD_PY_LEDGER}" "${ROUTER_RECORD_CS_LEDGER}" "router_record_ledger" "router --record ledger parity mismatch"
echo "PASS: run-with-it-router.cs record parity"

ROUTER_REVIEW_PY_OUTPUT="${TMP_ROOT}/router-review-py-output.json"
ROUTER_REVIEW_CS_OUTPUT="${TMP_ROOT}/router-review-cs-output.json"

python3 "${ROOT_DIR}/assets/python/run-with-it-router.py" \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${TMP_ROOT}/router-review-ledger.json" \
  --role review \
  --complexity-level medium-hard \
  --exclude-model gpt-5.3-codex \
  --detected-agents codex,agy,github-copilot,claude > "${ROUTER_REVIEW_PY_OUTPUT}"

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" -- \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${TMP_ROOT}/router-review-ledger.json" \
  --role review \
  --complexity-level medium-hard \
  --exclude-model gpt-5.3-codex \
  --detected-agents codex,agy,github-copilot,claude > "${ROUTER_REVIEW_CS_OUTPUT}"

compare_json_projection "${ROUTER_REVIEW_PY_OUTPUT}" "${ROUTER_REVIEW_CS_OUTPUT}" "router_review_output" "router review routing parity mismatch"
echo "PASS: run-with-it-router.cs review routing parity"

ARTIFACTS_PY_OUTPUT="${TMP_ROOT}/artifacts-py.txt"
ARTIFACTS_CS_OUTPUT="${TMP_ROOT}/artifacts-cs.txt"

python3 "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" failure-reason \
  --role modify \
  --issue 136 \
  --result-file "${TMP_ROOT}/missing-result.json" > "${ARTIFACTS_PY_OUTPUT}"

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-artifacts.cs" -- failure-reason \
  --role modify \
  --issue 136 \
  --result-file "${TMP_ROOT}/missing-result.json" > "${ARTIFACTS_CS_OUTPUT}"

compare_text_files "${ARTIFACTS_PY_OUTPUT}" "${ARTIFACTS_CS_OUTPUT}" "artifacts failure-reason parity mismatch"
echo "PASS: run-with-it-artifacts.cs failure-reason parity"

GITHUB_REPORT="${TMP_ROOT}/github-report.json"
cat > "${GITHUB_REPORT}" <<'JSON'
{
  "outcome": "completed",
  "summary": "Shipped deterministic helper parity.",
  "verification": {
    "passed": true,
    "commands_run": [
      "bash tests/run-with-it-csharp-smoke.test.sh"
    ],
    "evidence": "all smoke checks passed"
  },
  "review_summary": {
    "cycles_used": 1,
    "final_verdict": "approve",
    "reviewer_model": "codex/gpt-5.4"
  },
  "token_usage": {
    "input_tokens": 123,
    "output_tokens": 45,
    "cache_hit_tokens": 6
  },
  "commit_sha": "deadbeef",
  "merge": {
    "merge_sha": "cafebabe"
  }
}
JSON

GITHUB_RENDER_PY="${TMP_ROOT}/github-render-py.txt"
GITHUB_RENDER_CS="${TMP_ROOT}/github-render-cs.txt"

python3 "${ROOT_DIR}/assets/python/run-with-it-github-update.py" render-comment \
  --outcome completed \
  --report-file "${GITHUB_REPORT}" > "${GITHUB_RENDER_PY}"

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-github-update.cs" -- render-comment \
  --outcome completed \
  --report-file "${GITHUB_REPORT}" > "${GITHUB_RENDER_CS}"

compare_text_files "${GITHUB_RENDER_PY}" "${GITHUB_RENDER_CS}" "github render-comment parity mismatch"
echo "PASS: run-with-it-github-update.cs render-comment parity"

GITHUB_STATE_PY="${TMP_ROOT}/github-state-py.json"
GITHUB_STATE_CS="${TMP_ROOT}/github-state-cs.json"
cat > "${GITHUB_STATE_PY}" <<'JSON'
{"issue_registry":{"136":{"status":"completed"}}}
JSON
cp "${GITHUB_STATE_PY}" "${GITHUB_STATE_CS}"

GITHUB_UPDATE_PY="${TMP_ROOT}/github-update-py.txt"
GITHUB_UPDATE_CS="${TMP_ROOT}/github-update-cs.txt"

RUN_WITH_IT_GITHUB_UPDATES=0 \
python3 "${ROOT_DIR}/assets/python/run-with-it-github-update.py" update \
  --state-file "${GITHUB_STATE_PY}" \
  --run-root "${TMP_ROOT}" \
  --issue 136 \
  --outcome completed \
  --report-file "${GITHUB_REPORT}" > "${GITHUB_UPDATE_PY}"

RUN_WITH_IT_GITHUB_UPDATES=0 \
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-github-update.cs" -- update \
  --state-file "${GITHUB_STATE_CS}" \
  --run-root "${TMP_ROOT}" \
  --issue 136 \
  --outcome completed \
  --report-file "${GITHUB_REPORT}" > "${GITHUB_UPDATE_CS}"

compare_text_files "${GITHUB_UPDATE_PY}" "${GITHUB_UPDATE_CS}" "github update stdout parity mismatch"
compare_json_projection "${GITHUB_STATE_PY}" "${GITHUB_STATE_CS}" "github_update_state" "github update state parity mismatch"
echo "PASS: run-with-it-github-update.cs update parity"

PR_PY_OUTPUT="${TMP_ROOT}/pr-body-py.txt"
PR_CS_OUTPUT="${TMP_ROOT}/pr-body-cs.txt"

python3 "${ROOT_DIR}/assets/python/run-with-it-pr-body.py" render \
  --state-file "${STATE_PY}" > "${PR_PY_OUTPUT}"

dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-pr-body.cs" -- render \
  --state-file "${STATE_CS}" > "${PR_CS_OUTPUT}"

compare_text_files "${PR_PY_OUTPUT}" "${PR_CS_OUTPUT}" "pr-body render parity mismatch"
echo "PASS: run-with-it-pr-body.cs render parity"

# Argparse-compatible options check (using = syntax)
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-state.cs" -- mark-in-progress \
  --state-file="${STATE_CS}" \
  --issue=136 \
  --context-file="${COMMON_CONTEXT}" \
  --issue-dir="${COMMON_ISSUE_DIR}" \
  --pid=123 \
  --log-file="${COMMON_LOG}" \
  --done-file="${COMMON_DONE}" \
  --report-file="${STATE_REPORT}" >/dev/null

echo "PASS: run-with-it-state.cs argparse-compatible options"

# Concurrency/lock check: pre-create the lock file and verify the helper blocks
LOCK_FILE="${ROUTER_RECORD_CS_LEDGER}.lock"
touch "${LOCK_FILE}"

# Run the C# helper in the background with a recorded change
# It should block waiting for the lock
dotnet run "${ROOT_DIR}/assets/csharp/run-with-it-router.cs" -- \
  --registry-file "${ROOT_DIR}/assets/agent-registry.json" \
  --ledger-file "${ROUTER_RECORD_CS_LEDGER}" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude \
  --record > "${TMP_ROOT}/router-blocked-output.json" 2>&1 &
BG_PID=$!

# Wait briefly and verify the process is still running (blocked)
sleep 1
if ! kill -0 "${BG_PID}" 2>/dev/null; then
  fail "C# router exited early instead of blocking on the lock"
fi

# Release the lock by deleting the lock file
rm -f "${LOCK_FILE}"

# Wait for the background process to finish
wait "${BG_PID}"

echo "PASS: run-with-it-router.cs exclusive lock blocking and release"

echo "ALL C# SMOKE TESTS PASSED"
