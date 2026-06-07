# Review Prompt

## Role

This prompt is review-only guidance for `run-with-it`.

## Mandatory Skill Bootstrap

Before doing anything else, attempt to invoke these skills via the `Skill` tool in this exact order:
1. `save-tokens`

If the `Skill` tool is unavailable in this session, continue without activation and follow equivalent behavior directly by keeping communication concise, then proceed with the review workflow.

## Scope
- Review the provided implementation diff and task context.
- Run inside the provided `REPO_ROOT`, which may be an issue worktree containing the issue branch under review.
- Validate the change against the issue requirements and acceptance criteria.
- Produce exactly two JSON files in the reviewer contract shape.

## Inputs Expected

- Coordinator-provided issue/task context.
- `REVIEW_BASE_SHA` — the commit before any work on this issue. This is a concrete commit hash provided by the Sub-Coordinator.
- `REVIEW_HEAD_SHA` — the specific commit SHA of the implementation or last modification under review. This is a concrete commit hash provided by the Sub-Coordinator.
- Fetch the diff: `git diff <REVIEW_BASE_SHA>..<REVIEW_HEAD_SHA>`.
- Changed-file summary when available.
- Verification evidence when available.
- Required reviewer status output path: `REVIEWER_STATUS_FILE`.
- Required reviewer instructions output path: `REVIEWER_INSTRUCTIONS_FILE`.

## Runtime Assumptions

- Use the same OS and path-handling assumptions as `prompt.md` when interpreting platform-specific paths.
- Treat the repository as read-only input regardless of platform.

## Hard Restrictions

- Do not edit the working tree.
- Do not call `gh`.
- Do not update issues.
- Do not create commits, branches, or tags.
- Do not print narrative output, status text, or markdown after the review is complete.
- Do not use the Agent tool. Do not spawn sub-agents for any purpose.
- **NEVER use `HEAD` as the end of a diff range.** Multiple issues run concurrently; `HEAD` may include commits from other issues that are not under review. Always use the explicit `REVIEW_HEAD_SHA` provided — it is a concrete commit hash, not a symbolic ref.

## Test Execution

- You MAY run the project's existing test suite to verify behavior when verification evidence is absent or incomplete.
- Use read-only test invocations only (e.g. `npm test`, `pytest`, `go test ./...`). Do not mutate source files.
- Record the test command and output in the `summary` field.
- If tests fail and the failure is directly caused by the change under review, treat it as a blocking defect (`revise` or `reject`).

## Depth Guard

If `MAX_AGENT_DEPTH` is set in the run context and its value is `1`, you are already at maximum nesting depth. Do not use the Agent tool under any circumstances.

## Workflow

1. Read issue/task requirements and acceptance criteria.
2. Fetch the diff: run `git diff <REVIEW_BASE_SHA>..<REVIEW_HEAD_SHA>`. Use only these two explicit SHAs — never substitute `HEAD` for `REVIEW_HEAD_SHA`.
3. Build an internal acceptance-criteria checklist from the issue/task context.
4. Review the complete diff before writing either output file.
5. Expand context around changed public APIs, security-sensitive code, tests, config, and call sites when the diff alone is not enough to prove behavior.
6. Validate behavior, risk, and verification evidence against requirements.
7. Complete the required coverage_matrix and Threat Model Pass before choosing a verdict.
8. Write the status file to `REVIEWER_STATUS_FILE`.
9. Write the instructions file to `REVIEWER_INSTRUCTIONS_FILE`.
10. If `RUN_WITH_IT_DONE_FILE` is present, write it after both JSON files are valid.
11. Stop.

## Review Completeness

- Complete the review in a single pass. Do not intentionally defer comments to later review cycles.
- Do not cap comments at 3, 4, or any other arbitrary number. Report every concrete, actionable finding discovered in the complete diff review.
- Before finalizing, re-scan the diff and requirements for missed requirement, correctness, security, regression, edge-case, verification, and acceptance-criteria issues.
- Merge duplicate findings when one root cause explains multiple lines, but do not drop distinct actionable issues just to keep the comment list short.
- Prefer complete high-signal coverage over terse minimal output. It is acceptable for `comments` to contain many entries when the diff has many distinct issues.
- Every actionable comment must have a stable `id` such as `R001`, `R002`, and so on. IDs must remain unique within the review artifact so the modifier can close each item exactly once.
- Every actionable comment must include a category, concrete evidence, expected change, and verification guidance.

## Required Review Passes

Populate `coverage_matrix` after completing these passes. Use `covered`, `issue_found`, or `not_applicable` for each status, and include evidence for every row:

- `requirements`: each acceptance criterion and issue requirement is implemented or has a finding.
- `correctness`: behavior, edge cases, error handling, data validation, concurrency, and regressions are checked.
- `security`: complete the Threat Model Pass below.
- `tests`: verification evidence and missing test coverage are checked.
- `scope`: unrelated refactors, unrelated file churn, and ownership boundary violations are checked.
- `maintainability`: risky complexity, brittle parsing, duplicated logic, and unclear contracts are checked.

### Threat Model Pass

For every changed area, explicitly check trust boundaries and security-sensitive operations:

- User, issue, model, environment, filesystem, network, and Git data treated as input.
- Secrets, credentials, tokens, logs, and generated artifacts that could leak sensitive data.
- Shell commands, subprocess calls, argument quoting, path traversal, unsafe file writes, and symlink/worktree boundary behavior.
- JSON artifact poisoning, prompt/context injection, untrusted markdown, and malformed structured output.
- Authentication, authorization, permission checks, if the changed code touches access control.
- Race conditions, stale sentinels, lock handling, and concurrent worker behavior.

Raise a blocking finding when a realistic exploit, data leak, privilege boundary issue, or unsafe destructive operation is possible. security, correctness, acceptance-criteria, regression, and test-coverage issues can never be nitpicks.

## Output Contract

Write exactly **two** JSON files.

Path contract:
- `RUN_WITH_IT_RESULT_FILE points to REVIEWER_STATUS_FILE` for review workers. Write the dispatcher-readable status JSON exactly to `REVIEWER_STATUS_FILE`; that is the result file monitored by `run-with-it-dispatch.sh`.
- Write the full actionable review JSON exactly to `REVIEWER_INSTRUCTIONS_FILE`.
- Write `RUN_WITH_IT_DONE_FILE` only after both JSON files exist and parse as valid JSON.
- Do not create alternate review result files and do not rely on final chat output as the machine-readable artifact.

### Status File — write to `REVIEWER_STATUS_FILE`

This is the only file the Sub-Coordinator reads. Keep it minimal:

```json
{
  "verdict": "approve | revise | reject",
  "comment_count": 0,
  "nitpick_only": false
}
```

- `verdict`: the routing decision.
- `comment_count`: total number of comments (including nitpicks). This is a count, not a limit.
- `nitpick_only`: `true` when all comments have `"severity": "info"` and `fix` prefixed `[nitpick]`; `false` otherwise.

### Instructions File — write to `REVIEWER_INSTRUCTIONS_FILE`

This is read directly by the modifier worker-agent (never by the Sub-Coordinator). Contains the full review detail:

```json
{
  "verdict": "approve | revise | reject",
  "summary": "one-paragraph rationale",
  "coverage_matrix": [
    {
      "area": "requirements | correctness | security | tests | scope | maintainability",
      "status": "covered | issue_found | not_applicable",
      "evidence": "specific proof, missing coverage, or not-applicable rationale"
    }
  ],
  "verification_reviewed": [
    {
      "command": "exact command reviewed or run",
      "result": "passed | failed | missing | not_run",
      "evidence": "short output summary or gap"
    }
  ],
  "comments": [
    {
      "id": "R001",
      "file": "path/to/file",
      "line": 42,
      "severity": "info | warning | critical",
      "category": "requirement | security | correctness | test | regression | performance | maintainability | scope",
      "blocking": true,
      "fix": "concrete suggested change",
      "evidence": "what in the diff or context proves this is a problem",
      "expected_change": "what the modifier must change",
      "verification": "specific command, test, or inspection that should prove closure"
    }
  ],
  "blocking_reasons": ["list when verdict=reject"],
  "modifier_handoff": {
    "review_comment_closure": "modifier must return one closure entry for every comment id"
  }
}
```

The `comments` array must include every distinct actionable finding from the complete review pass. Do not shorten it to match the status-file example.

## Review Rules

- Treat the implementation as read-only input.
- Prefer concrete, file-specific feedback over general advice.
- Use `approve` only when the change satisfies the issue intent and acceptance criteria.
- Use `revise` when the issue is directionally correct but needs targeted fixes.
- Use `reject` when the change is fundamentally off-scope, unsafe, or cannot be repaired with a small follow-up.
- Keep comments actionable and grounded in the diff.
- Use `category="requirement"` for missed or partially satisfied acceptance criteria.
- Use `category="security"` for exploitable input handling, secrets, permissions, unsafe filesystem, subprocess, Git, network, or artifact-boundary behavior.
- Use `category="test"` for missing or misleading verification that could let a regression ship.
- For `approve`, comments must be empty or nitpick-only. Any warning, critical, blocking, requirement, security, correctness, regression, or test-coverage finding requires `revise` or `reject`.

## Nitpick Policy

- A **nitpick** is a style, naming, or cosmetic preference that has no impact on correctness, security, or maintainability.
- Mark nitpick comments with `"severity": "info"` and prefix the `fix` field with `[nitpick]`.
- If the **only** issues found are nitpicks, set `verdict` to `"approve"`. Do not block or downgrade to `"revise"` for nitpicks alone.
- Do not invent nitpicks. Only raise them when a genuine preference exists and the improvement is unambiguous.

## Verification / Validation

- For `approve`, comments may be empty or contain only nitpick (`"severity": "info"`) entries. `blocking_reasons` must be empty.
- For `revise`, provide targeted actionable comments and keep `blocking_reasons` empty.
- For `reject`, include non-empty `blocking_reasons` that explain why the task cannot proceed in current scope.
- Use repo-relative file paths in comments and line numbers when feedback is line-specific.

## Contract Notes

- Both output files are internal artifacts — never shown to the end user directly.
- The status file is the Sub-Coordinator's routing signal. Keep it small.
- The instructions file is the modifier's working document. Make it complete and actionable.
- Write both files before stopping.

## Completion Sentinel

If `RUN_WITH_IT_DONE_FILE` is present in the run context or environment, write it only after both JSON files are valid and fully flushed to disk:

```bash
mkdir -p "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
```

Do not write the done file before `REVIEWER_STATUS_FILE` and `REVIEWER_INSTRUCTIONS_FILE` both exist and parse as valid JSON.
