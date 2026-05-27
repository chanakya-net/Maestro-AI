# Run With It Dispatch and Handoff Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix both observed run-with-it failures: background dispatchers dying after `dispatch-ready`, and modifier/implementer workers writing machine-readable results to the wrong artifact path.

**Architecture:** Keep worker lifecycle ownership in the platform dispatcher and make sub-coordinators launch dispatcher processes through a durable detached wrapper instead of a shell `&` job that can die with the invoking shell. Keep final sub-coordinator reports and worker result artifacts strictly separated by removing report-path leakage from worker payloads and adding validation that rejects report-path writes as worker handoffs.

**Tech Stack:** Bash, PowerShell parity, Python artifact helper, shell contract tests under `tests/`, prompt contracts under `assets/`.

---

## File Structure

- Modify `assets/run-with-it-dispatch.sh`: add detached launch mode for long-running worker dispatches, pre-start failure trapping, and safer state temp cleanup.
- Modify `assets/run-with-it-dispatch.ps1`: mirror detached/pre-start behavior for Windows parity where practical.
- Modify `assets/sub-coordinator-prompt.md`: replace raw background `&` worker launch snippets with detached dispatcher invocation; remove `SUB_COORD_REPORT_FILE` from worker payload context; explicitly provide worker artifact paths.
- Modify `assets/coordinator-rules.md`: document detached dispatch, worker result/report separation, and failure classification.
- Modify `assets/prompt.md`: make implementer result-file instructions unambiguous and warn against writing `SUB_COORD_REPORT_FILE`.
- Modify `assets/modifier-prompt.md`: same for modifier workers.
- Modify `assets/run-with-it-artifacts.py`: reject worker result files that equal issue `report.json`, and add an optional recovery rule for logs only if the expected result file is absent but the worker wrote a valid worker-shaped JSON to the wrong path.
- Modify `tests/run-with-it-dispatch.test.sh`: cover detached dispatch survival and pre-start failure reporting.
- Modify `tests/run-with-it-routing.test.sh`: cover prompt/rules contracts for detached launch and path separation.
- Modify `tests/run-agent.test.sh`: cover implementer/modifier prompt language around `RUN_WITH_IT_RESULT_FILE`.
- Modify `tests/run-with-it-dispatch-ps1.test.sh` and `tests/run-with-it-routing-windows.test.sh` if PowerShell text changes.

## Task 1: Reproduce Dispatcher Death From Parent Shell Exit

**Files:**
- Modify: `tests/run-with-it-dispatch.test.sh`
- Read: `assets/run-with-it-dispatch.sh`

- [ ] **Step 1: Add a failing test that launches dispatcher from a short-lived shell**

Add this test near the existing live dispatcher smoke tests in `tests/run-with-it-dispatch.test.sh`:

```bash
DETACH_PROJECT="${WORK_DIR}/detach-project"
DETACH_CONTEXT="${DETACH_PROJECT}/context.md"
DETACH_PROMPT="${DETACH_PROJECT}/prompt.md"
DETACH_LOG="${DETACH_PROJECT}/workers/impl/cycle-1.log"
DETACH_DONE="${DETACH_PROJECT}/workers/impl/cycle-1.done"
DETACH_RESULT="${DETACH_PROJECT}/workers/impl/cycle-1-result.json"
DETACH_STATE="${DETACH_PROJECT}/workers/impl/cycle-1.state.json"
mkdir -p "${DETACH_PROJECT}/workers/impl"
printf 'detached context\n' > "${DETACH_CONTEXT}"
printf 'detached prompt\n' > "${DETACH_PROMPT}"

(
  cd "${DETACH_PROJECT}"
  PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh" \
    --asset-root "${SMOKE_ASSET_ROOT}" \
    --role impl \
    --issue 46 \
    --cycle 1 \
    --agent fake-agent \
    --model fake-model \
    --context-file "${DETACH_CONTEXT}" \
    --prompt-file "${DETACH_PROMPT}" \
    --log-file "${DETACH_LOG}" \
    --done-file "${DETACH_DONE}" \
    --result-file "${DETACH_RESULT}" \
    --state-file "${DETACH_STATE}" \
    --repo-root "${SMOKE_REPO_ROOT}" \
    --issue-dir "${DETACH_PROJECT}" \
    --poll-seconds 1 \
    --detach >"${DETACH_PROJECT}/dispatch.out" 2>&1
)

for _ in 1 2 3 4 5; do
  if grep -Fq "STATUS|type=dispatch-complete|issue=46|role=impl" "${DETACH_LOG}" 2>/dev/null; then
    break
  fi
  sleep 1
done

assert_file_contains "${DETACH_LOG}" "STATUS|type=dispatch-start|issue=46|role=impl|cycle=1" "detached dispatcher starts after parent shell exits"
assert_file_contains "${DETACH_LOG}" "STATUS|type=dispatch-pid|issue=46|role=impl|cycle=1" "detached dispatcher captures runner pid"
assert_json_file "${DETACH_STATE}" "detached dispatcher writes final state JSON"
assert_json_file "${DETACH_RESULT}" "detached worker writes result JSON"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
```

Expected: FAIL because `run-with-it-dispatch.sh` does not yet support `--detach`.

## Task 2: Add Detached Dispatcher Mode

**Files:**
- Modify: `assets/run-with-it-dispatch.sh`
- Test: `tests/run-with-it-dispatch.test.sh`

- [ ] **Step 1: Add parser flags and guard variables**

In `assets/run-with-it-dispatch.sh`, add variables near the current mode flags:

```bash
DETACH=0
DETACHED_CHILD="${RUN_WITH_IT_DETACHED_CHILD:-0}"
DISPATCH_OUT_FILE=""
```

Add parser cases:

```bash
--detach) DETACH=1; shift ;;
--dispatch-out-file) DISPATCH_OUT_FILE="${2:-}"; shift 2 ;;
```

- [ ] **Step 2: Implement self-detach before `dispatch-ready`**

After path validation and directory creation, before `write_status "STATUS|type=dispatch-ready..."`, add:

```bash
if [ "$DETACH" = 1 ] && [ "$DETACHED_CHILD" != "1" ]; then
  if [ -z "$DISPATCH_OUT_FILE" ]; then
    DISPATCH_OUT_FILE="${LOG_FILE%.log}.dispatch.out"
  fi
  mkdir -p "$(dirname "$DISPATCH_OUT_FILE")"
  RUN_WITH_IT_DETACHED_CHILD=1 nohup "$0" "$@" --dispatch-out-file "$DISPATCH_OUT_FILE" \
    >"$DISPATCH_OUT_FILE" 2>&1 < /dev/null &
  detached_pid="$!"
  write_status "STATUS|type=dispatch-detached|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${detached_pid}|out_file=${DISPATCH_OUT_FILE}"
  exit 0
fi
```

Important implementation note: preserve original arguments before shifting. The easiest safe approach is to add this at the top before the `while` parser:

```bash
ORIGINAL_ARGS=("$@")
```

Then invoke:

```bash
RUN_WITH_IT_DETACHED_CHILD=1 nohup "$0" "${ORIGINAL_ARGS[@]}" \
  >"$DISPATCH_OUT_FILE" 2>&1 < /dev/null &
```

Do not append `--dispatch-out-file` if it is already present; use the parsed value only for redirection.

- [ ] **Step 3: Run the dispatcher test**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
```

Expected: PASS including the new detached test.

## Task 3: Add Pre-Start Failure Classification

**Files:**
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `tests/run-with-it-dispatch.test.sh`

- [ ] **Step 1: Write failing test for state temp write crash**

Add a test that uses an unwritable state path after validation has passed:

```bash
PRESTART_DIR="${WORK_DIR}/prestart-project"
PRESTART_LOG="${PRESTART_DIR}/cycle-1.log"
PRESTART_DONE="${PRESTART_DIR}/cycle-1.done"
PRESTART_RESULT="${PRESTART_DIR}/cycle-1-result.json"
PRESTART_CONTEXT="${PRESTART_DIR}/context.md"
PRESTART_PROMPT="${PRESTART_DIR}/prompt.md"
mkdir -p "${PRESTART_DIR}/state-parent" "${PRESTART_DIR}/readonly"
printf 'context\n' > "${PRESTART_CONTEXT}"
printf 'prompt\n' > "${PRESTART_PROMPT}"
chmod 500 "${PRESTART_DIR}/readonly"

set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 47 \
  --cycle 1 \
  --agent fake-agent \
  --model fake-model \
  --context-file "${PRESTART_CONTEXT}" \
  --prompt-file "${PRESTART_PROMPT}" \
  --log-file "${PRESTART_LOG}" \
  --done-file "${PRESTART_DONE}" \
  --result-file "${PRESTART_RESULT}" \
  --state-file "${PRESTART_DIR}/readonly/cycle-1.state.json" \
  --issue-dir "${PRESTART_DIR}" \
  --poll-seconds 1 >"${PRESTART_DIR}/dispatch.out" 2>&1
prestart_status="$?"
set -e
chmod 700 "${PRESTART_DIR}/readonly"

[[ "${prestart_status}" != "0" ]] || fail "pre-start state failure must not exit success"
assert_file_contains "${PRESTART_LOG}" "STATUS|type=dispatch-ready|issue=47|role=complexity|cycle=1" "pre-start failure reaches ready"
assert_file_contains "${PRESTART_LOG}" "STATUS|type=dispatch-pre-start-failed|issue=47|role=complexity|cycle=1" "pre-start failure is classified"
assert_not_contains "$(cat "${PRESTART_LOG}")" "STATUS|type=dispatch-start|issue=47|role=complexity|cycle=1" "pre-start failure never starts runner"
```

- [ ] **Step 2: Add trap around ready-to-start window**

In `assets/run-with-it-dispatch.sh`, set a phase variable before ready:

```bash
dispatch_phase="pre-ready"
```

Add trap:

```bash
on_dispatch_error() {
  local exit_code="$?"
  if [ "${dispatch_phase:-}" = "ready-state" ]; then
    write_status "STATUS|type=dispatch-pre-start-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|reason=state-write-failed|exit_code=${exit_code}|state_file=${STATE_FILE}"
  fi
  exit "$exit_code"
}
trap on_dispatch_error ERR
```

Set phase around state write:

```bash
write_status "STATUS|type=dispatch-ready|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}|result_file=${RESULT_FILE}"
last_log_signature="$(log_signature)"
dispatch_phase="ready-state"
write_worker_state "ready" "false"

if [ "$VALIDATE_ONLY" = 1 ]; then
  dispatch_phase="validate-complete"
  exit 0
fi

dispatch_phase="starting"
write_status "STATUS|type=dispatch-start|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}"
```

- [ ] **Step 3: Run test**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
```

Expected: PASS and the new failure has `dispatch-pre-start-failed`.

## Task 4: Update Sub-Coordinator Launch Snippets To Use Detached Dispatch

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `assets/coordinator-rules.md`
- Test: `tests/run-with-it-routing.test.sh`

- [ ] **Step 1: Add prompt contract assertions**

In `tests/run-with-it-routing.test.sh`, add assertions:

```bash
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" '--detach' "sub-coordinator launches workers with detached dispatcher"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" 'Do not pass `SUB_COORD_REPORT_FILE` to worker payloads' "sub-coordinator keeps report path out of worker payloads"
assert_file_contains "$COORDINATOR_RULES_FILE" 'Worker result files must never be `$SUB_COORD_REPORT_FILE`' "coordinator rules separate worker result and final report"
```

- [ ] **Step 2: Modify Bash snippets**

In each worker dispatch snippet in `assets/sub-coordinator-prompt.md` for complexity, impl, review, and modify, add:

```bash
  --detach \
```

Keep `WORKER_PID=$!` language only if the command itself is still backgrounded. Preferred replacement:

```bash
"$ASSET_ROOT/run-with-it-dispatch.sh" \
  ... \
  --stall-seconds "$WORKER_STALL_SECONDS" \
  --detach

WORKER_PID="$(python3 - "$WORKER_STATE_FILE" <<'PY'
import json, sys, time
path = sys.argv[1]
for _ in range(50):
    try:
        data = json.load(open(path))
        print(data.get("dispatcher_pid") or "")
        raise SystemExit(0)
    except Exception:
        time.sleep(0.1)
print("")
PY
)"
```

If the state file is not yet available, the Sub-Coordinator should monitor `WORKER_STATE_FILE` and log `dispatch-pre-start-failed` when present.

- [ ] **Step 3: Update coordinator rules**

Add to `assets/coordinator-rules.md`:

```markdown
- Launch worker dispatchers with `--detach` when invoking them from a short-lived shell/tool call. A raw background `&` job may receive shell job-control cleanup before it writes `dispatch-start`.
- Worker result files must never be `$SUB_COORD_REPORT_FILE` or `.run-with-it/issues/<n>/report.json`; those paths are reserved for the Sub-Coordinator's final compact report.
```

- [ ] **Step 4: Run routing contracts**

Run:

```bash
bash tests/run-with-it-routing.test.sh
```

Expected: PASS.

## Task 5: Remove Report Path Leakage From Worker Payloads

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Test: `tests/run-with-it-routing.test.sh`

- [ ] **Step 1: Add assertion that worker payload excludes `SUB_COORD_REPORT_FILE`**

Add:

```bash
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" 'Worker payloads must include `RUN_WITH_IT_RESULT_FILE=' "worker payload names result path explicitly"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" 'Worker payloads must not include `SUB_COORD_REPORT_FILE`' "worker payload excludes final report path"
```

- [ ] **Step 2: Update payload instructions**

In `assets/sub-coordinator-prompt.md`, add near worker payload assembly:

```markdown
Worker payloads must include these artifact paths at the top:

```text
RUN_WITH_IT_RESULT_FILE=<absolute .run-with-it/issues/<n>/workers/<role>/cycle-<cycle>-result.json>
RUN_WITH_IT_DONE_FILE=<absolute .run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.done>
RUN_WITH_IT_ROLE=<impl|review|modify|complexity>
RUN_WITH_IT_ISSUE=<n>
```

Worker payloads must not include `SUB_COORD_REPORT_FILE` or instruct workers to write `.run-with-it/issues/<n>/report.json`. That file is reserved for the Sub-Coordinator's final compact report.
```

- [ ] **Step 3: Run routing test**

Run:

```bash
bash tests/run-with-it-routing.test.sh
```

Expected: PASS.

## Task 6: Strengthen Implementer and Modifier Prompts

**Files:**
- Modify: `assets/prompt.md`
- Modify: `assets/modifier-prompt.md`
- Test: `tests/run-agent.test.sh`

- [ ] **Step 1: Add prompt assertions**

In `tests/run-agent.test.sh`, after existing prompt contract assertions, add:

```bash
assert_contains "${prompt_contract}" 'Never write implementation handoff JSON to SUB_COORD_REPORT_FILE' "implementation prompt forbids report path handoff"
assert_contains "${modifier_contract}" 'Never write modification handoff JSON to SUB_COORD_REPORT_FILE' "modifier prompt forbids report path handoff"
assert_contains "${modifier_contract}" 'If RUN_WITH_IT_RESULT_FILE and SUB_COORD_REPORT_FILE differ, RUN_WITH_IT_RESULT_FILE wins' "modifier prompt resolves path ambiguity"
```

- [ ] **Step 2: Update `assets/prompt.md`**

In the Result Artifact section, add:

```markdown
Path safety:
- Never write implementation handoff JSON to `SUB_COORD_REPORT_FILE`.
- Never write implementation handoff JSON to `.run-with-it/issues/<n>/report.json`.
- If `RUN_WITH_IT_RESULT_FILE` and `SUB_COORD_REPORT_FILE` both appear in context and differ, `RUN_WITH_IT_RESULT_FILE` wins.
```

- [ ] **Step 3: Update `assets/modifier-prompt.md`**

In the Result Artifact section, add:

```markdown
Path safety:
- Never write modification handoff JSON to `SUB_COORD_REPORT_FILE`.
- Never write modification handoff JSON to `.run-with-it/issues/<n>/report.json`.
- If `RUN_WITH_IT_RESULT_FILE` and `SUB_COORD_REPORT_FILE` both appear in context and differ, `RUN_WITH_IT_RESULT_FILE` wins.
```

- [ ] **Step 4: Run prompt contract tests**

Run:

```bash
bash tests/run-agent.test.sh
```

Expected: PASS.

## Task 7: Guard Artifact Synthesis Against Wrong-Path Results

**Files:**
- Modify: `assets/run-with-it-artifacts.py`
- Modify: `tests/run-with-it-dispatch.test.sh`

- [ ] **Step 1: Add regression test for wrong report path**

In `tests/run-with-it-dispatch.test.sh`, add a fake worker that writes a valid modify result to issue `report.json` instead of `RUN_WITH_IT_RESULT_FILE`:

```bash
cat > "${SMOKE_BIN}/wrong-path-modifier" <<'SH'
#!/usr/bin/env bash
set -e
repo_root="${REPO_ROOT:-$(pwd -P)}"
mkdir -p "$repo_root"
git -C "$repo_root" init -q
git -C "$repo_root" config user.email test@example.com
git -C "$repo_root" config user.name Test
printf 'change\n' > "$repo_root/wrong-path.txt"
git -C "$repo_root" add wrong-path.txt
git -C "$repo_root" commit -q -m 'wrong path change'
commit_sha="$(git -C "$repo_root" rev-parse HEAD)"
wrong_file="${RUN_WITH_IT_ISSUE_DIR}/report.json"
printf '{"schema_version":1,"issue":"%s","role":"modify","status":"success","commit_sha":"%s","files_committed":["wrong-path.txt"],"verification":{"passed":true,"commands":["fake pass"]}}\n' "${RUN_WITH_IT_ISSUE}" "$commit_sha" > "$wrong_file"
printf 'DONE|issue=%s|role=modify|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/wrong-path-modifier"
```

Register it in the smoke registry, run dispatcher, then assert:

```bash
assert_file_contains "${WRONG_PATH_OUTPUT}" "reason=missing-result-artifact" "wrong report path does not count as worker result"
assert_not_contains "$(cat "${WRONG_PATH_RESULT}" 2>/dev/null || true)" '"verification":{"passed":true' "dispatcher does not trust wrong-path report as worker result"
```

- [ ] **Step 2: Make artifact helper explicitly identify wrong-path risk**

In `assets/run-with-it-artifacts.py`, add helper:

```python
def is_issue_report_path(path: Path, issue_dir: Path | None) -> bool:
    if issue_dir is None:
        return False
    try:
        return path.resolve() == (issue_dir / "report.json").resolve()
    except OSError:
        return False
```

Pass `--issue-dir` to artifact helper from dispatcher, or infer `issue_dir` from `RUN_WITH_IT_ISSUE_DIR` when available.

If `result_file` equals report path for `impl` or `modify`, return failure reason:

```python
return "worker-result-path-is-sub-coordinator-report"
```

- [ ] **Step 3: Run dispatcher tests**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
```

Expected: PASS.

## Task 8: PowerShell Parity

**Files:**
- Modify: `assets/run-with-it-dispatch.ps1`
- Modify: `tests/run-with-it-dispatch-ps1.test.sh`
- Modify: `tests/run-with-it-routing-windows.test.sh`

- [ ] **Step 1: Add Windows contract tests**

Add assertions equivalent to Bash:

```bash
assert_contains "$dry_output" "-Detach" "PowerShell dry-run documents detached dispatch"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" "-Detach" "PowerShell worker launch uses detached dispatcher"
```

- [ ] **Step 2: Add `-Detach` switch to PowerShell dispatcher**

In `assets/run-with-it-dispatch.ps1`, add parameter:

```powershell
[switch]$Detach,
[string]$DispatchOutFile = ""
```

Before writing `dispatch-ready`, if `$Detach` and not `$env:RUN_WITH_IT_DETACHED_CHILD`, use `Start-Process` to start PowerShell with the same dispatcher args, redirect output to `$DispatchOutFile`, write `STATUS|type=dispatch-detached`, and exit.

- [ ] **Step 3: Run Windows tests**

Run:

```bash
bash tests/run-with-it-dispatch-ps1.test.sh
bash tests/run-with-it-routing-windows.test.sh
```

Expected: PASS.

## Task 9: Full Verification

**Files:**
- All changed files.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
bash tests/run-agent.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
bash tests/run-with-it-routing-windows.test.sh
```

Expected: all PASS.

- [ ] **Step 2: Run broader run-with-it tests**

Run:

```bash
for test in tests/run-with-it-*.test.sh tests/run-agent*.test.sh tests/worker-watch*.test.sh; do
  bash "$test"
done
```

Expected: all PASS.

- [ ] **Step 3: Manual smoke against a disposable workspace**

Run a fake sub-coordinator flow that dispatches complexity, impl, review, and modify workers from short-lived shells. Expected:

```text
dispatch-ready
dispatch-detached
dispatch-start
dispatch-pid
agent-start
dispatch-complete
```

No zero-byte `*.state.json.tmp.*` files should remain for successful dispatches. No worker should write `.run-with-it/issues/<n>/report.json`.

## Self-Review

Spec coverage:
- Background dispatcher death is covered by Tasks 1-4 and Task 9.
- Worker result/report path confusion is covered by Tasks 5-7.
- PowerShell parity is covered by Task 8.
- Regression tests are included before implementation in each area.

Placeholder scan:
- No tasks rely on unspecified "add tests" or "handle errors"; each task gives exact files, commands, and expected behavior.

Type/name consistency:
- Bash uses `--detach`; PowerShell uses `-Detach`.
- Worker result path remains `RUN_WITH_IT_RESULT_FILE`.
- Final sub-coordinator report remains `SUB_COORD_REPORT_FILE`.
