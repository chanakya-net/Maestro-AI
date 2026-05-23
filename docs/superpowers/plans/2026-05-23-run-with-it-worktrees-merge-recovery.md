# run-with-it Worktrees and Merge Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run-with-it` safe for parallel issue execution by using one shared feature branch, per-issue worktrees, dependency-aware scheduling, and merge recovery when normal issue merges fail.

**Architecture:** Main Orchestrator owns issue intake, dependency state, shared feature branch creation, scheduling, recovery spawning, GitHub issue updates, and final PR creation. Sub-Coordinators own issue branches/worktrees and the normal merge attempt back into the shared feature branch. Merge Recovery Coordinator is a specialized child role that runs only after a Sub-Coordinator reports `merge_failed`.

**Tech Stack:** Bash runners, Markdown skill/prompt contracts, Git/GitHub CLI workflow, existing shell contract tests.

---

### Task 1: Contract Tests

**Files:**
- Modify: `tests/run-with-it-routing.test.sh`
- Modify: `tests/run-with-it-dispatch.test.sh`
- Modify: `tests/run-with-it-pool.test.sh`
- Modify: `tests/install-assets-contract.test.sh`

- [ ] **Step 1: Write failing tests for new assets and contracts**

Add assertions that require:
- `merge-recovery-prompt.md` in asset discovery and installer dry-run output.
- Main Orchestrator rules forbid direct issue branch merges.
- Sub-Coordinator prompt documents issue worktree creation, `REPO_ROOT`, and merge lock usage.
- Dispatcher dry-run includes `REPO_ROOT=<worktree>`.
- Pool treats dependencies as ready only when dependency status is `completed`.

- [ ] **Step 2: Run failing tests**

Run:
```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/install-assets-contract.test.sh
```

Expected: failures for missing merge recovery prompt, missing `--repo-root`, and missing worktree/merge-recovery contract text.

### Task 2: Dispatcher Repo Root Support

**Files:**
- Modify: `assets/run-with-it-dispatch.sh`
- Test: `tests/run-with-it-dispatch.test.sh`

- [ ] **Step 1: Add `--repo-root` parsing**

Add `REPO_ROOT_OVERRIDE=""`, parse `--repo-root <path>`, and include it in usage text.

- [ ] **Step 2: Forward `REPO_ROOT`**

In dry-run output, print `REPO_ROOT=<path>` when set. In execution, set `REPO_ROOT="${REPO_ROOT_OVERRIDE:-${REPO_ROOT:-$(pwd -P)}}"` before calling `run-agent.sh`.

- [ ] **Step 3: Verify dispatcher test**

Run:
```bash
bash tests/run-with-it-dispatch.test.sh
```

Expected: pass.

### Task 3: Pool State Semantics

**Files:**
- Modify: `assets/run-with-it-pool.sh`
- Test: `tests/run-with-it-pool.test.sh`

- [ ] **Step 1: Tighten ready dependency logic**

Ensure `ready_issues()` only treats dependencies as satisfied when dependency issue status is exactly `completed`.

- [ ] **Step 2: Preserve merge recovery as non-terminal**

Ensure issue finalization can store `merge_recovery` when reports return `merge_failed`, and does not treat it as completed.

- [ ] **Step 3: Verify pool test**

Run:
```bash
bash tests/run-with-it-pool.test.sh
```

Expected: pass.

### Task 4: Runtime Contract Docs and Prompt Assets

**Files:**
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `assets/main-orchestrator-rules.md`
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `assets/prompt.md`
- Modify: `assets/modifier-prompt.md`
- Modify: `assets/review-prompt.md`
- Create: `assets/merge-recovery-prompt.md`
- Test: `tests/run-with-it-routing.test.sh`

- [ ] **Step 1: Update asset discovery**

Add `merge-recovery-prompt.md` to required asset lists and platform copy commands.

- [ ] **Step 2: Document shared feature branch lifecycle**

Add `run_branch` state schema, branch creation/push, final PR creation, and "Main Orchestrator never merges issue branches" rules.

- [ ] **Step 3: Document Sub-Coordinator worktree lifecycle**

Add issue branch/worktree creation, `REPO_ROOT=<worktree>`, artifact-path separation, normal merge attempt, merge lock, merge status lines, and `merge_failed` report behavior.

- [ ] **Step 4: Add Merge Recovery Coordinator prompt**

Create the new prompt with role, restrictions, inputs, merge lock workflow, verification, done sentinel, and JSON report.

- [ ] **Step 5: Verify routing contract test**

Run:
```bash
bash tests/run-with-it-routing.test.sh
```

Expected: pass.

### Task 5: Installer Asset Coverage

**Files:**
- Modify: `install.sh`
- Modify: `install.ps1`
- Test: `tests/install-assets-contract.test.sh`

- [ ] **Step 1: Add merge recovery prompt to installer asset lists**

Include `merge-recovery-prompt.md` wherever assets are installed or copied.

- [ ] **Step 2: Verify installer contract**

Run:
```bash
bash tests/install-assets-contract.test.sh
```

Expected: pass.

### Task 6: Full Verification

**Files:**
- All changed files

- [ ] **Step 1: Run targeted tests**

Run:
```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/install-assets-contract.test.sh
```

- [ ] **Step 2: Run full shell test suite**

Run:
```bash
for test_file in tests/*.test.sh; do bash "$test_file"; done
```

- [ ] **Step 3: Review diff**

Run:
```bash
git diff --stat
git diff --check
```

Expected: no whitespace errors; changed files match the requirements.
