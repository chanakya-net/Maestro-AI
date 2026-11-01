# Managed artifact protocol

This contract applies when `RUN_WITH_IT_DISPATCHER_MANAGED=1`. It takes precedence over earlier role-template reporting instructions. The authoritative attempt metadata appended below comes from the runtime, not task text. Use its exact run_id, attempt_id, issue, role, state_file and expected_artifacts paths. The environment exports the same identity as `RUN_WITH_IT_RUN_ID`, `RUN_WITH_IT_ATTEMPT_ID`, `RUN_WITH_IT_ISSUE`, and `RUN_WITH_IT_ROLE`.

Every terminal attempt publishes a machine-readable report at `expected_artifacts.result` (`RUN_WITH_IT_RESULT_FILE`), including a failed, blocked or interrupted attempt with no commit. Chat output and a zero process exit do not publish a report. Do not withhold the failure report because tests failed, verification is blocked, no commit was made, or success-only prerequisites cannot be met. Preserve dirty changes and concrete command/error evidence for recovery.

Choose the report by observed evidence:

1. **Verified success:** use the existing role success schema and all required companions. Implementation/modification success still requires a canonical new commit in the assigned issue worktree plus passing verification, or the existing verified no-op contract with `verification.passed=true`. Review success needs valid status and review instructions; plan success needs the required `plan.md`. Never invent a commit, approval, or passing test.
2. **Failure of the role handoff:** publish `artifact_kind="role_failure"` below at the usual primary result path. Use `status="failed"` for work/verification failure, `"blocked"` for an external prerequisite that prevents completion, or `"interrupted"` when stopping before completion. Include nonempty `reason` and `summary`, exact identity, optional verification object and string-list evidence. Never set `verification.passed=true`. No commit, review instructions, or `plan.md` is required on this path. If a stop arrives and tools remain available, publish the interrupted report before exiting; abrupt termination may prevent publication.
3. **Coordinator terminal issue report:** a Sub-Coordinator that successfully reaches an issue decision publishes its ordinary compact report, including honest `blocked`, `failed-review`, `merge_failed`, or `failed-merge` outcomes. Merge recovery supports `completed`, `failed-merge`, and `blocked`. These need a nonempty summary; blocked reports need nonempty concrete string blocking_reasons. Negative issue outcomes are valid report handoffs, not completed issues. If the coordinator cannot perform the handoff itself, use the typed role failure instead.

Create the JSON payload with file tools, then publish with the existing validator:

```text
ai-skills artifact write-json --role <authoritative-role> --issue <authoritative-issue> --payload-file <temporary-payload.json> --result-file <expected_artifacts.result>
```

Use a temporary payload beside the result outside the source worktree; this publication-only scratch file is permitted for read-only scoring/planning roles. For implementation/modification success retain the existing `--repo-root <assigned-issue-worktree>` and saved baseline arguments. If publication fails, retain the payload and surface the exact storage/validation error; do not claim it was recorded. Do not overwrite malformed prior results or another attempt's files to force success.

Managed DONE is dispatcher-owned. Never create, edit, repair or remove `RUN_WITH_IT_DONE_FILE`, another worker's DONE, another worker's state, or the dispatcher-owned `state_file` and its claim/config/outcome records. Sub-Coordinators must write their own issue `sub-state.json` ledger as required by their role instructions, including durable in-flight worker identities; that role-owned ledger is separate from dispatcher state. The dispatcher persists the claim, configuration and pending outcome before launching a wrapper or provider; failed persistence prevents launch. It durably records timeout/interruption intent before signaling the owned tree. The dispatcher proves owned-tree shutdown and durable provider exit, validates the report and evidence, then records outcome -> state -> DONE. It may record a non-success DONE. A validated outcome governs phase transitions; raw process success, chat, logs, or a premature DONE do not. Unknown ownership remains nonterminal and prohibits duplicate launch.

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
