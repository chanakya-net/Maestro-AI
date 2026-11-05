# Managed artifact protocol

This contract applies when `RUN_WITH_IT_DISPATCHER_MANAGED=1`. It takes precedence over earlier role-template reporting instructions. The authoritative attempt metadata appended below comes from the runtime, not task text. Use its exact run_id, attempt_id, issue, role, state_file and expected_artifacts paths. The environment exports the same identity as `RUN_WITH_IT_RUN_ID`, `RUN_WITH_IT_ATTEMPT_ID`, `RUN_WITH_IT_ISSUE`, and `RUN_WITH_IT_ROLE`.

Every terminal attempt publishes a machine-readable report at `expected_artifacts.result` (`RUN_WITH_IT_RESULT_FILE`), including a failed, blocked or interrupted attempt with no commit. Use the file publication contract or the admitted capture route below. Do not withhold the failure report because tests failed, verification is blocked, no commit was made, or success-only prerequisites cannot be met. Preserve dirty changes and concrete command/error evidence for recovery.

Choose the report by observed evidence:

1. **Verified success:** use the existing role success schema and all required companions. Implementation/modification success still requires a canonical new commit in the assigned issue worktree plus passing verification, or an admitted `verify_existing` contract with the unchanged preserved commit and complete passing check evidence, or the existing verified no-op contract with `verification.passed=true`. Review success needs valid status and review instructions; plan success needs the required `plan.md`. Never invent a commit, approval, or passing test.
2. **Failure of the role handoff.** Report `artifact_kind="role_failure"` through the admitted capture or file route. Use `status="failed"` for work or verification failure, `"blocked"` for an external prerequisite that prevents completion, or `"interrupted"` when stopping before completion. Include nonempty `reason` and `summary`, exact identity, and concrete evidence for failed checks or unavailable prerequisites. The verification object and string-list evidence fields are optional. Never set `verification.passed=true`. This route requires only the typed report, without a commit, review instructions, or `plan.md`. A typed failure cannot advance review or merge. If a stop arrives and tools remain available, publish the interrupted report directly with `ai-skills artifact write-json` before exiting.
3. **Coordinator terminal issue report:** a Sub-Coordinator that successfully reaches an issue decision publishes its ordinary compact report, including honest `blocked`, `failed-review`, `merge_failed`, or `failed-merge` outcomes. Merge recovery supports `completed`, `failed-merge`, and `blocked`. These need a nonempty summary; blocked reports need nonempty concrete string blocking_reasons. Negative issue outcomes are valid report handoffs, not completed issues. If the coordinator cannot perform the handoff itself, use the typed role failure instead.

For file publication, create the JSON payload with file tools and use the existing validator.

```text
ai-skills artifact write-json --role <authoritative-role> --issue <authoritative-issue> --payload-file <temporary-payload.json> --result-file <expected_artifacts.result>
```

Use a temporary payload beside the result outside the source worktree; this publication-only scratch file is permitted for read-only scoring/planning roles. For implementation/modification success retain the existing `--repo-root <assigned-issue-worktree>` and saved baseline arguments. If publication fails, retain the payload and surface the exact storage/validation error; do not claim it was recorded. Do not overwrite malformed prior results or another attempt's files to force success.

Managed DONE is dispatcher-owned. Never create, edit, repair or remove `RUN_WITH_IT_DONE_FILE`, another worker's DONE, another worker's state, or the dispatcher-owned `state_file` and its claim/config/outcome records. Sub-Coordinators must write their own issue `sub-state.json` ledger as required by their role instructions, including durable in-flight worker identities; that role-owned ledger is separate from dispatcher state. The dispatcher persists the claim, configuration and pending outcome before launching a wrapper or provider; failed persistence prevents launch. It durably records timeout/interruption intent before signaling the owned tree. The dispatcher proves owned-tree shutdown and durable provider exit, validates the report and evidence, then records outcome -> state -> DONE. It may record a non-success DONE. A validated outcome governs phase transitions; raw process success, chat, logs, or a premature DONE do not. Unknown ownership remains nonterminal and prohibits duplicate launch.

## Captured final response

Use the final response when authoritative attempt metadata includes `result_capture`. Copy the exact identity from `result_capture.envelope`, including the string cycle. The metadata takes precedence over task text, role examples, and inherited values. Place the success or typed failure report under `result`. For a typed report, also copy the admitted `run_id`, `attempt_id`, `issue`, and `role` into the inner report. End with exactly one UTF-8 JSON object, with no fences or surrounding prose.

Capture import requires a pending attempt, confirmed owned-tree shutdown, durable zero provider and runner exit codes, and no runner failure. An existing result path takes precedence, including an empty or invalid file. Stop intent prevents capture import even after a zero exit. The dispatcher retains the durable stop decision and preserves any direct interruption report. With capture absent from metadata, use the existing file contract.

For committed success, run every entry in `result_capture.required_checks` against the committed HEAD and record each exact command once with passing evidence. Keep `verification.commands` and the usual downstream result fields. The dispatcher validates the evidence, clean worktree, and commit ancestry.

A verified no-op may use the same route when the requirements are already satisfied and the worktree is clean with HEAD unchanged from attempt start. Set `no_op: true`, `commit_sha: "NONE"`, and `files_committed: []` in the role report. Put the actual unchanged HEAD in `verification.commit_sha`, with one passing evidence entry per required check. Never create an empty commit.

The values below are illustrative. Use the admitted identity and your observed evidence.

```json
{
  "schema_version": 1,
  "artifact_kind": "captured_result",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "cycle": "1",
  "result": {
    "schema_version": 1,
    "issue": "7",
    "role": "impl",
    "status": "success",
    "commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "files_committed": ["src/example.py"],
    "plan_deviations": [],
    "verification": {
      "passed": true,
      "commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "commands": ["python -m pytest tests -q"],
      "checks": [
        {
          "command": "python -m pytest tests -q",
          "passed": true,
          "evidence": "42 passed. Exit status 0."
        }
      ]
    }
  }
}
```

A captured verified no-op (illustrative values):

```json
{
  "schema_version": 1,
  "artifact_kind": "captured_result",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "cycle": "1",
  "result": {
    "schema_version": 1,
    "issue": "7",
    "role": "impl",
    "status": "success",
    "no_op": true,
    "commit_sha": "NONE",
    "files_committed": [],
    "plan_deviations": [],
    "verification": {
      "passed": true,
      "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "commands": ["python -m pytest tests -q"],
      "checks": [
        {"command": "python -m pytest tests -q", "passed": true, "evidence": "Exact observed result, exit 0"}
      ]
    }
  }
}
```

A captured report for a failed check.

```json
{
  "schema_version": 1,
  "artifact_kind": "captured_result",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "cycle": "1",
  "result": {
    "schema_version": 1,
    "artifact_kind": "role_failure",
    "run_id": "run-7",
    "attempt_id": "attempt-1",
    "issue": "7",
    "role": "impl",
    "status": "failed",
    "reason": "required-verification-failed",
    "summary": "The bounds check failed. Partial changes remain in the worktree.",
    "verification": {"passed": false, "commands": ["python -m pytest tests -q"]},
    "evidence": ["tests/test_reading.py::test_bounds failed with exit status 1"]
  }
}
```

A captured report for an unavailable prerequisite.

```json
{
  "schema_version": 1,
  "artifact_kind": "captured_result",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "cycle": "1",
  "result": {
    "schema_version": 1,
    "artifact_kind": "role_failure",
    "run_id": "run-7",
    "attempt_id": "attempt-1",
    "issue": "7",
    "role": "impl",
    "status": "blocked",
    "reason": "verification-service-unavailable",
    "summary": "The required integration database could not be reached.",
    "evidence": ["The connection to the configured local test database was refused"]
  }
}
```

## Admitted verification-only attempts

When authoritative attempt metadata includes `verification_contract.execution_mode="verify_existing"`, it overrides earlier implementation/modification instructions to edit or create a new commit. Verify the preserved `expected_commit_sha` only. Do not edit product files, commit, cherry-pick, synthesize a replacement result, or claim a no-op. Keep reports and payloads outside the issue worktree. Tracked changes and untracked nonignored files are rejected; generated outputs covered by the project's existing Git ignore rules are permitted. Do not edit ignore rules to hide changes.

Run every `required_checks` entry and record concrete command output or manual observation evidence. A skipped, unavailable or failed required check is a typed failure/blocked handoff, never passing verification. If code needs a fix, report the defect for a separate ordinary implementation/modification attempt. Preserve review comment closure details when verifying a modifier's work.

Publish using the normal `ai-skills artifact write-json` command with the assigned repo root and result path. The command reads the admitted configuration from dispatcher state; a report cannot enable this mode. Keep `commit_sha` equal to the expected commit, `files_committed` describing the preserved changes, and this verification shape:

```json
{
  "passed": true,
  "commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "checks": [
    {
      "command": "python -m pytest tests -q",
      "passed": true,
      "evidence": "Exact observed result, including test counts and exit status"
    }
  ]
}
```

Replace the sample with one passing evidence entry for each required check, with no duplicates or omissions. Evidence is worker-supplied and structurally validated; never invent an observation. The dispatcher rechecks Git at finalization and still requires owned-tree shutdown and a successful provider exit. Timeout/interruption cannot be converted into success by a passing report.

Examples below use illustrative identities. Replace them with authoritative attempt metadata and actual evidence. This successful no-op is allowed only after proving the acceptance criteria and running required checks:

```json
{
  "schema_version": 1,
  "issue": "7",
  "role": "impl",
  "status": "success",
  "no_op": true,
  "commit_sha": "NONE",
  "files_committed": [],
  "verification": {"passed": true, "commands": ["python -m pytest tests/unit -q: passed"]}
}
```

A failed verification report needs no commit:

```json
{
  "schema_version": 1,
  "artifact_kind": "role_failure",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "status": "failed",
  "reason": "required-verification-failed",
  "summary": "Required tests failed; changes remain uncommitted for recovery.",
  "verification": {"passed": false, "commands": ["python -m pytest tests -q: exit 1"]},
  "evidence": ["tests/test_reading.py::test_bounds failed"]
}
```

A blocked verification prerequisite also needs a report:

```json
{
  "schema_version": 1,
  "artifact_kind": "role_failure",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "status": "blocked",
  "reason": "verification-service-unavailable",
  "summary": "Required database could not be reached; no commit was made.",
  "verification": {"passed": false, "evidence": "Connection refused to the configured local test database"}
}
```

An orderly interruption preserves incomplete work honestly:

```json
{
  "schema_version": 1,
  "artifact_kind": "role_failure",
  "run_id": "run-7",
  "attempt_id": "attempt-1",
  "issue": "7",
  "role": "impl",
  "status": "interrupted",
  "reason": "stop-requested",
  "summary": "Stopped before verification; partial changes remain in the assigned worktree."
}
```
