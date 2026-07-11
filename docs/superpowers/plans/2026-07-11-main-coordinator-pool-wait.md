# Main Coordinator Pool Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Main Coordinator alive from Sub-Coordinator launch through the pool runner's terminal `pool-empty` event.

**Architecture:** The shared pool runner already owns scheduling and blocks until all active issues finish. Change the Main Coordinator contract to invoke that runner in a foreground long-lived tool session on Bash and PowerShell, and require the agent to resume the same yielded session rather than treating process launch as completion.

**Tech Stack:** Markdown agent instructions, Bash/PowerShell command examples, shell contract tests.

## Global Constraints

- Preserve `run-with-it-pool.sh` / `.ps1` as the single rolling-pool supervisor.
- Preserve detached Sub-Coordinator dispatchers and issue-scoped PID/artifact tracking.
- Keep `skills/run-with-it/SKILL.md` and `.agents/skills/run-with-it/SKILL.md` byte-for-byte equivalent.
- Do not include `debug_human_report.md` or `debug_llm_context.md` in the PR.

---

### Task 1: Enforce the Main Coordinator lifecycle contract

**Files:**
- Modify: `tests/run-with-it-routing.test.sh`
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `.agents/skills/run-with-it/SKILL.md`
- Modify: `assets/main-orchestrator-rules.md`

**Interfaces:**
- Consumes: `run-with-it-pool.sh` / `.ps1` command-line interfaces and the `STATUS|type=pool-empty` terminal event.
- Produces: a Main Coordinator instruction contract that cannot return immediately after launching the pool.

- [ ] **Step 1: Write the failing contract test**

Add assertions requiring the source skill to say the pool runs in the foreground, requiring same-session polling after a yielded tool call, forbidding the background-only `nohup` launch, requiring synchronous PowerShell invocation, and requiring the compact orchestrator rules to forbid treating launch as completion. Retain the existing source/install skill parity assertion.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `bash tests/run-with-it-routing.test.sh`

Expected: FAIL because the current Step D still contains `nohup ... &` / `Start-Process` and lacks a concrete foreground same-session contract.

- [ ] **Step 3: Implement the minimal instruction change**

Replace the Bash `nohup ... &` block with a foreground invocation of `run-with-it-pool.sh`. Replace the PowerShell `Start-Process -PassThru` block with direct `& powershell ...` execution and an explicit non-zero exit check. State that a yielded terminal/session ID must be resumed until the same process exits and `pool-empty` is observed. Add the equivalent hard rule to `assets/main-orchestrator-rules.md`, then copy the source skill change exactly to `.agents/skills/run-with-it/SKILL.md`.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `bash tests/run-with-it-routing.test.sh && bash tests/run-with-it-pool.test.sh && bash tests/run-with-it-pool-ps1.test.sh`

Expected: all three test scripts print PASS and exit 0.

- [ ] **Step 5: Run the complete repository test suite**

Run: `for test_file in tests/*.test.sh; do bash "$test_file" || exit 1; done`

Expected: every test script exits 0.

- [ ] **Step 6: Commit the focused change**

Stage only the plan, the routing test, both skill mirrors, and the compact orchestrator rules. Commit with `fix(run-with-it): keep main coordinator alive`.
