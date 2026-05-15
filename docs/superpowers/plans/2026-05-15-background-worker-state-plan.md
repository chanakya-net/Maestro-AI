# Background Worker State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Sub-Coordinators launch worker agents as monitored background jobs, persist in-flight worker metadata before spawn, and survive context compression without losing process visibility.

**Architecture:** The Sub-Coordinator owns phase decisions and artifact validation. A small shell helper reports process liveness and recent log tail metadata, while `.run-with-it/sub-<issue>-state.json` stores durable worker state. Completion is decided only by role-specific artifacts plus `RUN_WITH_IT_DONE_FILE`; logs are used only for progress summaries.

**Tech Stack:** Bash, PowerShell documentation parity, JSON state files, existing `run-agent.sh` / `run-agent.ps1`, shell contract tests.

---

## File Structure

- Create: `assets/worker-watch.sh`
  - Single-purpose helper for checking one worker PID, done file, and optional log tail metadata.
  - Emits one parseable `WORKER|...` line.
  - Does not decide whether a phase is complete.

- Modify: `assets/sub-coordinator-prompt.md`
  - Add a mandatory state bootstrap step before complexity spawn.
  - Replace foreground worker examples with background launch, PID capture, state write, 20-second liveness polling, and 60-second log summary.
  - Require state updates before every major phase transition.

- Modify: `assets/coordinator-rules.md`
  - Strengthen the durable-state rule.
  - Clarify PID, done-file, artifact, and log responsibilities.

- Modify: `tests/run-with-it-log-harness.test.sh`
  - Assert `.run-with-it/sub-101-state.json` is created before worker spawn.
  - Assert `in_flight_agents` includes worker PID, role, cycle, log file, done file, and result file.
  - Assert progress summaries are copied into the Sub-Coordinator log.

- Add or modify: `tests/worker-watch.test.sh`
  - Test `worker-watch.sh` for alive, dead, done-file, missing-log, and changed-tail cases.

- Modify: `tests/install-assets-contract.test.sh`
  - Ensure `worker-watch.sh` is treated as an installable asset.

- Modify: `install.sh` and `install.ps1`
  - Copy `worker-watch.sh` with the other assets.
  - On Unix, mark it executable.

- Modify: `README.md` and `explainer.html`
  - Document the background worker lifecycle after implementation passes.

---

## Task 1: Add Worker Watch Helper

**Files:**
- Create: `assets/worker-watch.sh`
- Test: `tests/worker-watch.test.sh`

- [ ] **Step 1: Write the failing test**

Create `tests/worker-watch.test.sh`:

```bash
#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHER="${ROOT_DIR}/assets/worker-watch.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${sleep_pid:-}" ]]; then
    kill "${sleep_pid}" 2>/dev/null || true
  fi
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

LOG_FILE="${WORK_DIR}/worker.log"
DONE_FILE="${WORK_DIR}/worker.done"
TAIL_STATE="${WORK_DIR}/tail.sha"

printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running tests\n' > "${LOG_FILE}"

sleep 30 &
sleep_pid="$!"

alive_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${alive_output}" 'WORKER|' 'watcher emits parseable prefix'
assert_contains "${alive_output}" 'alive=true' 'watcher reports live process'
assert_contains "${alive_output}" 'done=false' 'watcher reports missing done file'
assert_contains "${alive_output}" 'log_tail_changed=true' 'first log read is changed'

repeat_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${repeat_output}" 'log_tail_changed=false' 'unchanged tail is detected'

printf 'DONE|issue=42|role=impl|status=success|source=agent\n' > "${DONE_FILE}"
done_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${done_output}" 'done=true' 'watcher reports done file'

kill "${sleep_pid}" 2>/dev/null || true
wait "${sleep_pid}" 2>/dev/null || true
sleep_pid=""

dead_output="$("${WATCHER}" --pid 999999 --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${dead_output}" 'alive=false' 'watcher reports dead process'
assert_contains "${dead_output}" 'done=true' 'watcher still reports done file for dead process'

echo "PASS: worker-watch helper"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
bash tests/worker-watch.test.sh
```

Expected: FAIL because `assets/worker-watch.sh` does not exist.

- [ ] **Step 3: Implement the helper**

Create `assets/worker-watch.sh`:

```bash
#!/usr/bin/env bash

set -euo pipefail

PID=""
DONE_FILE=""
LOG_FILE=""
TAIL_STATE_FILE=""
TAIL_LINES="5"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pid)
      PID="${2:-}"
      shift 2
      ;;
    --done-file)
      DONE_FILE="${2:-}"
      shift 2
      ;;
    --log-file)
      LOG_FILE="${2:-}"
      shift 2
      ;;
    --tail-state-file)
      TAIL_STATE_FILE="${2:-}"
      shift 2
      ;;
    --tail-lines)
      TAIL_LINES="${2:-5}"
      shift 2
      ;;
    *)
      echo "worker-watch.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${PID}" || -z "${DONE_FILE}" ]]; then
  echo "worker-watch.sh: --pid and --done-file are required" >&2
  exit 2
fi

alive="false"
if kill -0 "${PID}" 2>/dev/null; then
  alive="true"
fi

done_present="false"
if [[ -s "${DONE_FILE}" ]]; then
  done_present="true"
fi

log_present="false"
log_tail_changed="false"
tail_hash=""

if [[ -n "${LOG_FILE}" && -s "${LOG_FILE}" ]]; then
  log_present="true"
  tail_text="$(tail -n "${TAIL_LINES}" "${LOG_FILE}")"
  if command -v shasum >/dev/null 2>&1; then
    tail_hash="$(printf '%s' "${tail_text}" | shasum -a 256 | awk '{print $1}')"
  else
    tail_hash="$(printf '%s' "${tail_text}" | cksum | awk '{print $1 "-" $2}')"
  fi

  previous_hash=""
  if [[ -n "${TAIL_STATE_FILE}" && -f "${TAIL_STATE_FILE}" ]]; then
    previous_hash="$(cat "${TAIL_STATE_FILE}")"
  fi

  if [[ "${tail_hash}" != "${previous_hash}" ]]; then
    log_tail_changed="true"
    if [[ -n "${TAIL_STATE_FILE}" ]]; then
      mkdir -p "$(dirname "${TAIL_STATE_FILE}")"
      printf '%s\n' "${tail_hash}" > "${TAIL_STATE_FILE}"
    fi
  fi
fi

printf 'WORKER|pid=%s|alive=%s|done=%s|log_present=%s|log_tail_changed=%s|tail_hash=%s\n' \
  "${PID}" "${alive}" "${done_present}" "${log_present}" "${log_tail_changed}" "${tail_hash:-none}"
```

- [ ] **Step 4: Make helper executable and run test**

Run:

```bash
chmod +x assets/worker-watch.sh
bash tests/worker-watch.test.sh
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add assets/worker-watch.sh tests/worker-watch.test.sh
git commit -m "test: add worker watch helper"
```

---

## Task 2: Add Mandatory State Bootstrap

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `tests/run-with-it-log-harness.test.sh`

- [ ] **Step 1: Add failing harness assertions**

In `tests/run-with-it-log-harness.test.sh`, add:

```bash
SUB_STATE_FILE="${PROJECT_DIR}/.run-with-it/sub-101-state.json"
```

After the Sub-Coordinator run assertions, add:

```bash
assert_file "${SUB_STATE_FILE}" "sub-coordinator state file exists"
assert_json_file "${SUB_STATE_FILE}" "sub-coordinator state file is valid JSON"
assert_contains "${SUB_STATE_FILE}" '"schema_version": 1' "state file has schema version"
assert_contains "${SUB_STATE_FILE}" '"in_flight_agents"' "state file tracks in-flight agents"
assert_contains "${SUB_STATE_FILE}" '"role": "impl"' "state file recorded implementation worker"
assert_contains "${SUB_STATE_FILE}" '"done_file"' "state file records done file paths"
assert_contains "${SUB_STATE_FILE}" '"log_file"' "state file records log file paths"
assert_contains "${SUB_STATE_FILE}" '"pid"' "state file records worker pid"
```

- [ ] **Step 2: Run harness to verify it fails**

Run:

```bash
bash tests/run-with-it-log-harness.test.sh
```

Expected: FAIL because the fake Sub-Coordinator does not create `.run-with-it/sub-101-state.json`.

- [ ] **Step 3: Update fake Sub-Coordinator in harness**

Inside the generated fake Sub-Coordinator script in `tests/run-with-it-log-harness.test.sh`, before `write_status "STATUS|type=sub-start|issue=${issue}"`, add:

```bash
SUB_STATE_FILE="${PROJECT_DIR}/.run-with-it/sub-${issue}-state.json"

write_state() {
  local phase="$1"
  local in_flight_json="${2:-[]}"
  cat > "${SUB_STATE_FILE}" <<JSON
{
  "schema_version": 1,
  "issue_number": ${issue},
  "phase": "${phase}",
  "in_flight_agents": ${in_flight_json},
  "review_history": [],
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
}
```

Call the bootstrap before any worker starts:

```bash
write_state "starting" "[]"
```

Inside `spawn_worker()`, after `spawned_pid="$!"`, add:

```bash
escaped_log="$(printf '%s' "${log_file}" | json_escape)"
escaped_done="$(printf '%s' "${done_file}" | json_escape)"
escaped_result="$(printf '%s' "${result_file}" | json_escape)"
write_state "${role}" "[{\"role\":\"${role}\",\"cycle\":${cycle},\"pid\":${spawned_pid},\"agent\":\"${agent}\",\"model\":\"fake-model\",\"log_file\":${escaped_log},\"done_file\":${escaped_done},\"result_file\":${escaped_result},\"started_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}]"
```

After the final `wait`, add:

```bash
write_state "report-written" "[]"
```

- [ ] **Step 4: Update the real Sub-Coordinator prompt**

In `assets/sub-coordinator-prompt.md`, add a section before complexity analysis:

```markdown
### Mandatory State Bootstrap

Before spawning the complexity worker, create `.run-with-it/sub-<SUB_COORD_ISSUE_NUMBER>-state.json`. This file is required even if the run later fails before the first worker starts.

Initial schema:

```json
{
  "schema_version": 1,
  "issue_number": 42,
  "phase": "starting",
  "in_flight_agents": [],
  "review_history": [],
  "updated_at": "2026-05-15T00:00:00Z"
}
```

Write this file before every major phase transition and immediately after every worker PID is captured. On context compression/resume, read this file first. If a listed worker still has no valid result artifact, use its stored `pid`, `done_file`, `log_file`, and `result_file` to decide whether to continue waiting, process completed artifacts, or re-spawn the phase.
```

- [ ] **Step 5: Run harness**

Run:

```bash
bash tests/run-with-it-log-harness.test.sh
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add assets/sub-coordinator-prompt.md tests/run-with-it-log-harness.test.sh
git commit -m "feat: persist sub coordinator worker state"
```

---

## Task 3: Convert Worker Launch Contract to Background Monitoring

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `assets/coordinator-rules.md`
- Modify: `tests/run-with-it-log-harness.test.sh`

- [ ] **Step 1: Add prompt contract assertions**

In `tests/run-with-it-routing.test.sh`, add assertions against `assets/sub-coordinator-prompt.md`:

```bash
assert_contains 'WORKER_PID=$!' "sub-coordinator captures worker PID"
assert_contains 'assets/worker-watch.sh' "sub-coordinator uses worker-watch helper"
assert_contains 'WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"' "sub-coordinator polls worker liveness every 20 seconds"
assert_contains 'WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"' "sub-coordinator summarizes logs every 60 seconds"
assert_contains 'done file and valid artifacts' "sub-coordinator requires done file and artifacts"
```

- [ ] **Step 2: Run routing test to verify it fails**

Run:

```bash
bash tests/run-with-it-routing.test.sh
```

Expected: FAIL because the prompt still documents foreground worker calls.

- [ ] **Step 3: Replace Bash worker invocation pattern in prompt**

For each Bash worker example in `assets/sub-coordinator-prompt.md` (complexity, impl, review, modify), change the launch shape to:

```bash
WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-${RUN_WITH_IT_ROLE}-cycle-${CYCLE:-1}.tail.sha"

GUI_MODE="${GUI_MODE:-0}" \
AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}" \
RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}" \
RUN_WITH_IT_LOG_FILE="$IMPL_LOG_FILE" \
RUN_WITH_IT_DONE_FILE="$IMPL_DONE_FILE" \
RUN_WITH_IT_ROLE="impl" \
RUN_WITH_IT_ISSUE="$SUB_COORD_ISSUE_NUMBER" \
"$ASSET_ROOT/run-agent.sh" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --context-file "$CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/prompt.md" \
  --unattended &

WORKER_PID=$!
```

Immediately after `WORKER_PID=$!`, require a state write with the captured PID and all paths.

- [ ] **Step 4: Add monitoring loop language**

In `assets/sub-coordinator-prompt.md`, add this contract:

```markdown
After spawning a background worker, monitor it until either:

1. `RUN_WITH_IT_DONE_FILE` exists and the role-specific artifacts are valid, or
2. the process exits without valid artifacts.

Every `WORKER_POLL_SECONDS` seconds, run `assets/worker-watch.sh` with the stored PID, done file, log file, and tail-state file. PID liveness is diagnostic only. Completion requires both the done file and valid artifacts.

Every `WORKER_LOG_SUMMARY_SECONDS` seconds, if the worker log tail changed, read only the newest `${WORKER_LOG_TAIL_LINES:-5}` lines, write a concise `STATUS|type=worker-log-tail|...` summary to `$SUB_COORD_LOG_FILE`, update `$RUN_WITH_IT_STATUS_FILE`, and append `$RUN_WITH_IT_EVENTS_LOG`. Do not store the raw log tail in memory or in the state file.

If the PID is dead, immediately `wait "$WORKER_PID"` to capture the runner exit code. If done file and artifacts are valid, continue to the next phase. If not, treat the phase as failed or follow the documented fallback chain for that phase.
```

- [ ] **Step 5: Strengthen coordinator rules**

In `assets/coordinator-rules.md`, replace the progress monitoring bullets with:

```markdown
- Start every worker-agent as a background process, capture `WORKER_PID=$!`, and persist that PID plus role, cycle, agent, model, log file, done file, and result file to `.run-with-it/sub-<issue>-state.json` before monitoring begins.
- Poll worker liveness every `WORKER_POLL_SECONDS` seconds, default `20`.
- Summarize worker log progress every `WORKER_LOG_SUMMARY_SECONDS` seconds, default `60`, using only the newest `${WORKER_LOG_TAIL_LINES:-5}` lines.
- PID death does not automatically mean failure. If the process is dead, inspect only the done file and required artifacts. If they are valid, proceed. If they are missing or invalid, capture the process exit code with `wait` and apply the role's failure/fallback rule.
- Logs never decide completion. Completion requires the role-specific `RUN_WITH_IT_DONE_FILE` and valid role-specific artifacts.
```

- [ ] **Step 6: Run tests**

Run:

```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-log-harness.test.sh
bash tests/worker-watch.test.sh
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add assets/sub-coordinator-prompt.md assets/coordinator-rules.md tests/run-with-it-routing.test.sh tests/run-with-it-log-harness.test.sh tests/worker-watch.test.sh
git commit -m "feat: monitor workers as background jobs"
```

---

## Task 4: Install Asset and Documentation Parity

**Files:**
- Modify: `install.sh`
- Modify: `install.ps1`
- Modify: `tests/install-assets-contract.test.sh`
- Modify: `README.md`
- Modify: `explainer.html`

- [ ] **Step 1: Add install contract assertions**

In `tests/install-assets-contract.test.sh`, assert dry-run output includes:

```bash
assert_contains "${dry_run_output}" "worker-watch.sh" "dry-run includes worker watcher asset"
```

- [ ] **Step 2: Run install contract to verify it fails**

Run:

```bash
bash tests/install-assets-contract.test.sh
```

Expected: FAIL because install scripts do not include `worker-watch.sh`.

- [ ] **Step 3: Update install scripts**

In `install.sh`, add `worker-watch.sh` to the asset file list and add:

```bash
chmod +x "${ASSETS_DEST}/worker-watch.sh"
```

In `install.ps1`, add `worker-watch.sh` to the copied asset list.

- [ ] **Step 4: Update docs**

In `README.md`, document:

```markdown
- `worker-watch.sh`: helper used by Sub-Coordinators to check background worker liveness and log-tail changes. It does not decide phase completion.
- `.run-with-it/sub-<issue>-state.json`: durable Sub-Coordinator state created before the first worker spawn and updated after every worker PID capture.
```

In `explainer.html`, update the Sub-Coordinator internals section to show:

```text
spawn background worker -> capture PID -> write state -> poll every 20s -> summarize logs every 60s -> validate done file + artifacts -> proceed
```

- [ ] **Step 5: Run install/docs tests**

Run:

```bash
bash tests/install-assets-contract.test.sh
bash tests/run-with-it-routing.test.sh
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add install.sh install.ps1 tests/install-assets-contract.test.sh README.md explainer.html
git commit -m "docs: document background worker lifecycle"
```

---

## Task 5: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused shell tests**

Run:

```bash
bash tests/worker-watch.test.sh
bash tests/run-agent-status-bus.test.sh
bash tests/run-with-it-log-harness.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/install-assets-contract.test.sh
```

Expected: all PASS.

- [ ] **Step 2: Run complete shell test suite**

Run:

```bash
for test_file in tests/*.test.sh; do
  bash "${test_file}"
done
```

Expected: every test prints PASS and exits 0.

- [ ] **Step 3: Review state-file behavior manually**

Use the log harness temp output while debugging, or temporarily disable cleanup, and confirm:

```text
.run-with-it/sub-101-state.json exists before first worker spawn
in_flight_agents[0].pid is numeric
in_flight_agents[0].log_file points at the active role log
in_flight_agents[0].done_file points at the role done sentinel
phase changes as the Sub-Coordinator moves through complexity, impl, review, modify, and report-written
```

- [ ] **Step 4: Commit any verification-only fixes**

```bash
git status --short
git add <changed-files>
git commit -m "test: verify background worker lifecycle"
```

Only commit if Step 1 or Step 2 required fixes.

---

## Design Notes

- The state file must be created before the first worker starts. This fixes the current gap where compaction can occur before any durable worker metadata exists.
- `worker-watch.sh` must stay dumb. It reports process/log facts; it must not validate role artifacts.
- Logs are not completion signals. They are progress signals only.
- `RUN_WITH_IT_DONE_FILE` is also not sufficient alone. Completion requires done file plus valid role artifacts.
- PID death is not automatically failure. A worker may finish correctly, write artifacts, and exit before the next poll.
- A live PID is not automatically progress. The 60-second log summary gives the user visibility without loading full logs into context.
- The Sub-Coordinator should keep a later `wait "$WORKER_PID"` result in state or log even if it already advanced based on valid artifacts. This preserves diagnostics for cleanup failures without blocking useful phase transitions.

## Self-Review

- Spec coverage: includes state bootstrap, background launch, PID persistence, 20-second liveness polling, 60-second log summaries, done/artifact completion, helper script, install assets, and tests.
- Placeholder scan: no unresolved placeholder markers.
- Type consistency: state fields use `schema_version`, `issue_number`, `phase`, `in_flight_agents`, `role`, `cycle`, `pid`, `agent`, `model`, `log_file`, `done_file`, `result_file`, and `started_at` consistently.
