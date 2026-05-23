# Technical Requirements: run-with-it Dependency Scheduling, Feature Branch Worktrees, and Merge Recovery

## Goal

Evolve `run-with-it` so it can safely execute dependency-aware GitHub issues in parallel while isolating each issue's implementation in its own git worktree and preserving one final pull request for the whole run.

The desired runtime model is:

1. `run-with-it` creates one shared feature branch for the full run.
2. Each Sub-Coordinator creates an issue-specific branch and worktree from that shared feature branch.
3. Sub-Coordinator child agents implement, review, modify, verify, and commit inside the issue worktree.
4. The Sub-Coordinator attempts to merge the issue branch back into the shared feature branch.
5. If that merge fails, Main Orchestrator moves the issue to `merge_recovery` and spawns a specialized Merge Recovery Coordinator.
6. When all runnable issues are terminal, Main Orchestrator creates one PR from the shared feature branch.

Main Orchestrator must never perform code merges itself.

## Resolved Decisions

- Use topological sorting over issue dependencies before execution.
- Use a shared run feature branch, e.g. `run-with-it/<run-id>`, created at run start from the original base branch.
- Push the shared feature branch to remote after every successful merge.
- Each Sub-Coordinator creates an issue branch and worktree from the shared feature branch, e.g. branch `run-with-it/<run-id>/issue-123`, worktree `.run-with-it/worktrees/issue-123`.
- Implementation, review, and modify workers run inside the issue worktree through `REPO_ROOT=<worktree_path>`.
- Sub-Coordinator attempts the normal merge back into the shared feature branch after the issue passes review.
- If Sub-Coordinator merge fails, the issue status becomes `merge_recovery`.
- Main Orchestrator continues scheduling unrelated ready issues while `merge_recovery` runs.
- Issues that depend on an issue in `merge_recovery` remain not-ready until recovery succeeds.
- Merge Recovery Coordinator is spawned only when Sub-Coordinator merge fails.
- Merge Recovery Coordinator has holistic access to the shared feature branch, the failed issue branch/worktree, relevant completed summaries, dependency context, and conflict/verification context.
- If Merge Recovery Coordinator succeeds, issue status becomes `completed`, dependency readiness is recalculated, and newly unblocked issues can enter the rolling pool.
- If recovery fails, issue status becomes `failed-merge` or `blocked`; dependents remain blocked with a reason pointing to the failed issue.

## Functional Requirements

### Issue Dependency Graph

- Parse dependencies from GitHub issue bodies and local fallback issue text.
- `create-git-issue` already emits a `## Blocked by` section; `run-with-it` must treat that section as the primary dependency source.
- Support common references in `## Blocked by`:
  - `None - can start immediately`
  - `#123`
  - full GitHub issue URLs
  - plain issue numbers when unambiguous
- Normalize dependencies to issue numbers present in the fetched issue set when possible.
- Preserve unresolved or external dependencies in state as blocking reasons.
- Detect cycles before execution. Cyclic issues must not run; mark them `blocked` with cycle details.
- Write `execution_plan.topo_order` to `.run-with-it/main-state.json`.
- Within the same dependency tier, preserve current priority behavior: critical fixes, infrastructure, feature slices, polish, refactors.

### Shared Run Feature Branch

- At run start, capture:
  - original base branch name
  - original base SHA
  - remote name, when available
  - shared run branch name
  - shared run branch start SHA
- Create the shared run branch before spawning Sub-Coordinators.
- Store shared branch metadata in `main-state.json`.
- Push the shared branch to remote at creation when a GitHub remote exists.
- The final PR must use the shared run branch as head and original base branch as base.
- Main Orchestrator may create/push the shared branch and open the final PR, but must not merge issue branches.

### Per-Issue Worktrees

- Each Sub-Coordinator creates an issue branch from the latest shared run feature branch.
- Each Sub-Coordinator creates a worktree under `.run-with-it/worktrees/issue-<n>`.
- Worktree paths must be absolute in context files and state.
- Sub-Coordinator must run all worker agents with `REPO_ROOT=<worktree_path>`.
- Logs, reports, status files, reviews, and done sentinels remain under the root checkout's `.run-with-it/`, not inside the issue worktree.
- Sub-Coordinator state must record:
  - `feature_branch`
  - `issue_branch`
  - `worktree_path`
  - `issue_base_sha`
  - `impl_commit_sha`
  - `modify_commit_sha`
  - `review_head_sha`
  - merge status and merge attempt metadata

### Normal Merge Path

- After issue implementation passes review, Sub-Coordinator attempts to merge the issue branch into the shared feature branch.
- Sub-Coordinator must acquire an exclusive merge lock before touching the shared feature branch.
- Lock path should live under `.run-with-it/locks/merge.lock`.
- While holding the lock, Sub-Coordinator must:
  1. fetch/pull latest shared feature branch when a remote exists
  2. update local shared feature branch
  3. merge the issue branch
  4. run required verification or a configured smoke check
  5. push the shared feature branch
  6. release the lock
- Sub-Coordinator emits merge status lines:
  - `STATUS|type=merge-start|issue=<n>|branch=<issue_branch>|target=<feature_branch>`
  - `STATUS|type=merge-complete|issue=<n>|merge_sha=<sha>|pushed=<true|false>`
  - `STATUS|type=merge-failed|issue=<n>|reason=<conflict|verification|push|unknown>`
- On successful merge, compact report outcome remains `completed` and includes `merge_sha`, `issue_branch`, `feature_branch`, and `worktree_path`.
- On failed merge, compact report outcome is `merge_failed` and includes conflict or verification evidence sufficient for Merge Recovery Coordinator.

### Merge Recovery State

- Add issue status `merge_recovery`.
- Main Orchestrator sets `merge_recovery` after reading a Sub-Coordinator report with `outcome=merge_failed`.
- `merge_recovery` is non-terminal.
- Dependency readiness treats `merge_recovery` as incomplete.
- Unrelated issues may continue to run.
- Dependents of the issue in `merge_recovery` must not be scheduled.

### Merge Recovery Coordinator

- Add a new prompt asset: `assets/merge-recovery-prompt.md`.
- Merge Recovery Coordinator runs only when Sub-Coordinator merge fails.
- It must be spawned by Main Orchestrator via `run-with-it-dispatch.sh` using a new role, `merge-recovery`.
- Main Orchestrator must not perform the merge itself.
- Merge Recovery Coordinator receives:
  - issue title/body/acceptance criteria
  - failed Sub-Coordinator compact report
  - shared feature branch name and path
  - failed issue branch and worktree path
  - dependency list and completed summaries for relevant dependencies
  - conflict files or merge failure summary
  - verification commands
  - required report path
- Merge Recovery Coordinator must:
  1. acquire the merge lock
  2. inspect shared feature branch and failed issue branch holistically
  3. resolve merge conflicts or failed merge state
  4. run verification
  5. commit the resolved merge to the shared feature branch
  6. push the shared feature branch
  7. write a compact merge recovery report
  8. release the merge lock
- Merge Recovery Coordinator must not close issues or update GitHub issue state.
- Merge Recovery Coordinator report outcomes:
  - `completed`
  - `failed-merge`
  - `blocked`
- On success, Main Orchestrator updates original issue status to `completed`, appends recovery summary, recalculates readiness, and may schedule dependents.
- On failure, Main Orchestrator updates original issue status to `failed-merge` or `blocked`, and dependent issues remain blocked.

### Final PR

- When all issues are terminal and no `merge_recovery` issues remain, Main Orchestrator creates one PR.
- PR head: shared run feature branch.
- PR base: original base branch captured at run start.
- PR body should include:
  - processed issue list
  - completed/blocked/failed counts
  - merge recovery summary
  - verification summary
  - links to closed/updated issues
- If PR creation fails, keep the shared branch and print the manual PR command.

## State Schema Requirements

### Main State Additions

Add or update `.run-with-it/main-state.json` fields:

```json
{
  "schema_version": 4,
  "run_branch": {
    "base_branch": "main",
    "base_sha": "abc123",
    "feature_branch": "run-with-it/2026-05-23-abcdef",
    "feature_branch_start_sha": "abc123",
    "remote": "origin",
    "pushed": true,
    "pr_url": null
  },
  "execution_plan": {
    "topo_order": [101, 102],
    "dependency_tiers": [[101], [102]],
    "parallel_jobs": 4,
    "execution_mode": "rolling-pool"
  },
  "issue_registry": {
    "101": {
      "status": "pending | in_progress | merge_recovery | completed | failed-review | failed-merge | blocked",
      "deps": [],
      "dependency_proof": "Blocked by: None",
      "issue_branch": "run-with-it/.../issue-101",
      "worktree_path": ".run-with-it/worktrees/issue-101",
      "merge_recovery_report_file": ".run-with-it/reports/merge-recovery-101-report.json",
      "blocking_reasons": []
    }
  }
}
```

### Sub-Coordinator Report Additions

Add report fields:

```json
{
  "issue_branch": "run-with-it/<run-id>/issue-101",
  "feature_branch": "run-with-it/<run-id>",
  "worktree_path": ".run-with-it/worktrees/issue-101",
  "merge": {
    "status": "completed | failed | skipped",
    "merge_sha": "abc123",
    "pushed": true,
    "failure_reason": null,
    "conflict_files": []
  }
}
```

### Merge Recovery Report

New compact report shape:

```json
{
  "schema_version": 1,
  "issue_number": 101,
  "outcome": "completed | failed-merge | blocked",
  "summary": "Resolved conflict between issue branch and shared feature branch.",
  "feature_branch": "run-with-it/<run-id>",
  "issue_branch": "run-with-it/<run-id>/issue-101",
  "merge_sha": "abc123",
  "files_modified": [
    { "path": "src/example.ts", "lines_added": 3, "lines_deleted": 1 }
  ],
  "verification": {
    "passed": true,
    "commands_run": ["npm test"],
    "evidence": "all tests passed"
  },
  "blocking_reasons": []
}
```

## Implementation Steps

### Main Orchestrator and Skill Contract

1. Update `skills/run-with-it/SKILL.md` to document the shared run feature branch lifecycle, topological dependency graph, `merge_recovery` state, Merge Recovery Coordinator, and final PR creation.
2. Update `assets/main-orchestrator-rules.md` to state that Main Orchestrator may create/push the shared run branch and open the PR, but must never merge issue branches.
3. Update initial issue planning instructions to parse `## Blocked by`, detect cycles, and write `execution_plan.topo_order`, `dependency_tiers`, and `dependency_proof`.
4. Update main loop readiness rules so dependencies are ready only when their statuses are `completed`.
5. Update main loop report handling so `merge_failed` moves the issue to `merge_recovery` and spawns Merge Recovery Coordinator.
6. Add final PR handoff instructions after all issues are terminal and the shared branch is pushed.

### Pool Runner and Dispatcher

1. Update `assets/run-with-it-pool.sh` dependency handling to treat only `completed` dependencies as satisfied.
2. Update `assets/run-with-it-pool.sh` to understand `merge_recovery` as active/non-terminal and not schedule dependents.
3. Update `assets/run-with-it-pool.sh` finalization so `merge_failed` does not become a terminal completed summary; it transitions to recovery handling.
4. Add `--repo-root` or `--worktree-path` support to `assets/run-with-it-dispatch.sh`.
5. Forward the chosen repo root as `REPO_ROOT=<path>` to `assets/run-agent.sh`.
6. Add role support for `merge-recovery` in dispatcher validation, dry-run output, logging, and tests.

### Sub-Coordinator Worktree and Merge

1. Update `assets/sub-coordinator-prompt.md` input contract to include `RUN_FEATURE_BRANCH`, `RUN_BASE_BRANCH`, `RUN_BASE_SHA`, `ISSUE_BRANCH`, `ISSUE_WORKTREE_PATH`, and absolute `.run-with-it` artifact paths.
2. Add a worktree bootstrap phase before complexity analysis.
3. Create issue branch and worktree from latest shared feature branch.
4. Store worktree metadata in `.run-with-it/sub-<issue>-state.json`.
5. Ensure every complexity/impl/review/modify dispatch receives `--repo-root "$ISSUE_WORKTREE_PATH"` while artifact paths remain rooted in the orchestrator checkout.
6. Capture commit and diff SHAs inside the issue worktree.
7. Add a normal merge phase after review approval.
8. Implement merge lock instructions and status lines.
9. On merge success, push shared feature branch and include merge metadata in compact report.
10. On merge failure, write `outcome=merge_failed` with conflict/verification/push failure details.

### Merge Recovery Coordinator

1. Create `assets/merge-recovery-prompt.md`.
2. Define role, scope, hard restrictions, input contract, workflow, verification requirements, merge lock behavior, push behavior, done sentinel, and output report.
3. Require `save-tokens` and `tdd-implementation` where appropriate for conflict resolution changes.
4. Require all edits and merge resolution to occur while holding the merge lock.
5. Require valid JSON report before writing done sentinel.
6. Add Merge Recovery Coordinator artifact paths:
   - `.run-with-it/merge-recovery/issue-<n>.log`
   - `.run-with-it/done/issue-<n>-merge-recovery.done`
   - `.run-with-it/reports/merge-recovery-<n>-report.json`

### Implementation and Modifier Prompts

1. Update `assets/prompt.md` to state implementation runs inside the provided `REPO_ROOT`, which may be an issue worktree.
2. Update mandatory commit wording to say commits happen on the issue branch in the issue worktree.
3. Update `assets/modifier-prompt.md` the same way.
4. Update `assets/review-prompt.md` to clarify that review diff commands run in the issue worktree or recovery context provided by `REPO_ROOT`.

### Cleanup

1. Update cleanup policy to remove `.run-with-it/worktrees/` only after successful completion or explicit discard.
2. Remove issue worktrees with `git worktree remove` when possible.
3. Preserve worktrees, branches, reports, and logs after failed/interrupted runs.
4. Do not delete the shared feature branch after final PR creation.
5. Document manual cleanup commands for preserved issue branches/worktrees.

### Tests

1. Add contract tests for dependency parsing from `## Blocked by`.
2. Add contract tests for topological order and cycle detection.
3. Update `tests/run-with-it-pool.test.sh` for statuses `merge_recovery` and `failed-merge`.
4. Add dispatcher dry-run tests proving `--repo-root` is forwarded as `REPO_ROOT`.
5. Add documentation contract tests requiring `merge-recovery-prompt.md` in asset discovery.
6. Add tests requiring Main Orchestrator rules to forbid direct merges.
7. Add tests requiring Sub-Coordinator prompt to create issue worktrees and use merge lock.
8. Add tests requiring final PR instructions and shared feature branch state schema.

## Non-Functional Requirements

- Must remain compaction-safe: all branch, worktree, dependency, and merge state must be persisted before spawning or monitoring agents.
- Must be crash-recoverable: pushed shared branch and persisted state should allow resume without losing completed work.
- Must preserve the existing no-log-loading rule: Main Orchestrator reads compact reports only.
- Must preserve bounded context: merge recovery receives compact summaries and targeted conflict context, not full unrelated logs.
- Must remain safe for `PARALLEL_JOBS > 1`.
- Must avoid destructive git operations unless explicitly scoped to `.run-with-it` worktrees or run-created branches.

## Open Items

- [HITL] Confirm exact branch naming format for `run-with-it/<run-id>` and issue branches.
- [HITL] Confirm whether final PR should be draft by default. Recommended: create draft PR.
- [HITL] Confirm whether Merge Recovery Coordinator may run full test suite by default or only configured verification commands. Recommended: configured commands first, full suite when practical.

## Handoff

Requirements are ready. The next step is to run the `create-git-issue` skill to turn this into implementation slices.
