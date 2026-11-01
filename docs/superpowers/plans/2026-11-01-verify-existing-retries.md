# Verification-only retries implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete verification of preserved implementation/modification commits without an empty commit or unnecessary artifact recovery.

**Architecture:** Keep `pre_spawn_head` as the actual starting HEAD. Add dispatcher-owned `execution_mode=verify_existing` and a persisted retry contract. Reuse a single read-only validator for publication and finalization; keep ordinary implementation and verified no-op semantics unchanged.

**Tech Stack:** Python 3.12+, Git, pytest, existing process supervisor and outcome journal.

**Spec:** Approved plan and review in this chat, captured below.

## Requirements and constraints

- A creates baseline; a worker commits B then reports verification blocked; a fresh verification attempt starts and ends at B and reports B. Resume the normal review policy using A..B.
- Apply equally to impl and modify; preserve review comments, cycle, routing history and original issue baseline.
- Retry admission requires the exact source attempt, same issue/worktree/role/cycle, durable terminal outcome and confirmed provider/dispatcher death. Unknown provenance uses recovery.
- Persist the contract before launch. Only dispatcher configuration enables the exception; reports cannot grant themselves permission.
- Require complete passing evidence for every configured check, bound to B. A report's boolean alone is insufficient.
- Reject changed HEAD and dirty tracked/untracked product files. Existing Git-ignored generated outputs are allowed; runtime artifacts live outside the issue worktree. Do not introduce blanket source exclusions.
- Never cherry-pick, synthesize success, or auto-commit under verification-only mode. Defects require a new modification attempt.
- Keep source reports/outcomes immutable, use fresh paths, and preserve stop/exit-status semantics on restart.
- No active installation or pinned run is changed. Ship Python and maintained prompts together.

## Review focus

- Stale/wrong source attempt or previous modification cycle cannot validate unrelated work.
- Publication and finalization cannot disagree or fall through to Git salvage.
- A timeout or crash must not publish success, including after provider exit 0.
- Missing, duplicate, skipped, failed, or wrong-commit evidence cannot pass.
- Restart/adoption must preserve the admitted contract and previously journaled decision.

## Tasks

### 1. Runtime contract and regression
Files: `src/ai_skills/{dispatch,runner,artifacts,verification}.py`, `tests/integration/test_verification_retry.py`.
- [x] Write and run failing two-attempt tests for impl and modify using real Git and subprocess providers.
- [x] Add validated configuration, terminal Git provenance, source admission checks, runtime prompt metadata, and shared publication/finalization validation.
- [x] Test unchanged-commit acceptance plus ordinary-implementation rejection and source/HEAD/evidence failures.

### 2. Recovery and restart
Files: the same runtime modules and integration tests.
- [x] Test publication cannot cherry-pick and timeout cannot commit dirty work in this mode.
- [x] Test fresh paths, source liveness, durable contracts, restart/adoption and immutable previous artifacts.
- [x] Test verification success reaches reviewer with original accumulated range and merge handoff in a disposable repository.

### 3. Coordinator and packaging
Files: `assets/{coordinator-rules,sub-coordinator-prompt,artifact-protocol,prompt,modifier-prompt}.md`.
- [x] Document selection, authoritative contract, passing evidence, bounded blocked retries and defect-to-modification routing.
- [x] Reconcile recovery rules and no-op instructions; preserve existing review gates and packaging.
- [ ] Run full suites on local supported interpreters, independent contextual review, resolve findings, and open a PR. Check native matrix CI.

## Execution notes

- Ruling: use an explicit nested `retry_contract` plus `issue_base_sha` in WorkerConfig. The contract also identifies the source state file so a source attempt ID resolves unambiguously without scanning other runs.
- Ruling: verification evidence remains worker-supplied command/manual-check evidence, validated for completeness and commit identity; this is not an independent execution engine or a security sandbox against a malicious local worker.

- Baseline: Python 3.12 suite 953 passed, 5 skipped.
- Regression red: both impl/modify publication failed with missing-implementation-commit.
- Focused runtime/protocol/artifact suite: 142 passed. Includes deterministic subprocess providers, real Git, actual review dispatch and native offline merge. Model-driven coordinator selection still depends on the maintained instructions; no paid live model run is implied.
- Ruling: journal verified handoff with contract/report fingerprint so later review modifications cannot invalidate a completed attempt during adoption. A focused downstream-advance test failed before this addition and passes afterward.
- Independent contextual review found three issues: cross-attempt writable path aliases, missing detached-wrapper evidence, and failed-terminal replay after worktree recovery. Each was reproduced with a failing test and fixed. Final independent review found no remaining actionable findings; six replay regressions were independently rerun successfully.
- Reviewed regression suite: 77 passed. Final full interpreter and installed-wheel validation recorded in the PR.
- A simultaneous local full-suite run exposed an existing `test_invoking_process_exit_leaves_independent_adoptable_attempts[True]` fixture race: it reads identity before publication. Its leftover fixture processes were stopped by exact supervisor identity. Final full suites run sequentially; no unrelated process implementation change was made.
