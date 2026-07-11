# Sub-Coordinator Worker Routing Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Sol as the fixed Sub-Coordinator runtime without forcing Sol onto automatically routed child workers.

**Architecture:** Separate control-plane runtime selection (`SUB_COORD_AGENT`, `SUB_COORD_MODEL`) from worker-policy overrides (`FORCED_AGENT`, `FORCED_MODEL`). Normalize deprecated `AGENT` and `MODEL` aliases only when the user explicitly supplied them, never from ambient dispatcher/runtime values.

**Tech Stack:** Markdown agent contracts, Bash contract tests, PowerShell parity assertions, Python routing helper.

## Global Constraints

- `SUB_COORD_AGENT` and `SUB_COORD_MODEL` select only the Sub-Coordinator runtime.
- `FORCED_AGENT` and `FORCED_MODEL` are the canonical child-worker overrides.
- `AGENT` and `MODEL` remain deprecated aliases only for explicitly supplied user overrides.
- Sol remains the default Sub-Coordinator model.
- Model weights, complexity bands, usage targets, and recovery behavior remain unchanged.

---

### Task 1: Lock the routing contract with failing tests

**Files:**
- Modify: `tests/run-with-it-routing.test.sh`
- Modify: `tests/run-with-it-pool.test.sh`
- Modify: `tests/run-with-it-pool-ps1.test.sh`

**Interfaces:**
- Consumes: Existing static contract assertion helpers and `run-with-it-router.py` CLI.
- Produces: Regression assertions requiring canonical worker override names and forbidding coordinator-runtime leakage.

- [ ] **Step 1: Add routing contract assertions**

Add assertions requiring the Step C environment block and override precedence section to use `FORCED_AGENT` and `FORCED_MODEL`. Add forbidden assertions for `AGENT=<value-if-set>`, `MODEL=<value-if-set>`, and the old ``AGENT` + `MODEL` forced together` wording.

- [ ] **Step 2: Add the easy-route counterfactual**

Invoke `run-with-it-router.py` with a temporary empty ledger, `--role impl`, `--complexity-level easy`, and Codex-only agent constraints, but no forced model. Assert the selected model is not `gpt-5.6-sol` and the reason is not `forced-agent-and-model`.

- [ ] **Step 3: Add pool contract assertions**

Require Bash and PowerShell pool tests to retain `gpt-5.6-sol` as the Sub-Coordinator default while asserting their documentation does not describe that value as a child override.

- [ ] **Step 4: Run tests and verify RED**

Run:

```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
```

Expected: routing contract assertions fail because the current skill and prompt still use `AGENT` and `MODEL` as child overrides.

### Task 2: Separate runtime and worker override contracts

**Files:**
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `assets/main-orchestrator-rules.md`
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Canonical `FORCED_AGENT` and `FORCED_MODEL` router arguments already accepted by `run-with-it-router.py`.
- Produces: Context files containing canonical worker overrides only when explicitly requested.

- [ ] **Step 1: Update the skill input contract**

Document `FORCED_AGENT` and `FORCED_MODEL` as canonical inputs. Mark `AGENT` and `MODEL` as deprecated aliases accepted only when explicitly named by the user, never read from ambient process state.

- [ ] **Step 2: Update Step C context generation**

Replace `AGENT=<value-if-set>` and `MODEL=<value-if-set>` with `FORCED_AGENT=<explicit-worker-override-if-set>` and `FORCED_MODEL=<explicit-worker-override-if-set>`. Add an invariant forbidding `SUB_COORD_*` values from populating these fields.

- [ ] **Step 3: Update orchestration and Sub-Coordinator rules**

State that the pool dispatcher’s `--agent` and `--model` values configure only the Sub-Coordinator process. Change override precedence to `FORCED_AGENT` and `FORCED_MODEL`; explicitly classify ambient `AGENT` and `MODEL` as runner telemetry, not routing policy.

- [ ] **Step 4: Update README documentation**

Mirror the canonical override names, compatibility note, and coordinator/worker separation in user-facing configuration documentation.

- [ ] **Step 5: Run targeted tests and verify GREEN**

Run:

```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
```

Expected: all three scripts exit 0 and print their PASS lines.

### Task 3: Verify the full routing surface

**Files:**
- Verify: `assets/run-with-it-router.py`
- Verify: `assets/run-with-it-pool.sh`
- Verify: `assets/run-with-it-pool.ps1`
- Verify: all changed documentation and tests

**Interfaces:**
- Consumes: Completed contract changes from Tasks 1 and 2.
- Produces: Evidence that existing routing, pool, and dispatcher behavior remains valid.

- [ ] **Step 1: Run focused router and dispatcher suites**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: all scripts exit 0.

- [ ] **Step 2: Run the full contract suite**

Run:

```bash
for test_file in tests/*.test.sh; do bash "$test_file"; done
```

Expected: every test script exits 0.

- [ ] **Step 3: Check formatting and scope**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only routing-contract documentation and tests are modified.

- [ ] **Step 4: Commit the implementation**

```bash
git add README.md skills/run-with-it/SKILL.md assets/main-orchestrator-rules.md assets/sub-coordinator-prompt.md tests/run-with-it-routing.test.sh tests/run-with-it-pool.test.sh tests/run-with-it-pool-ps1.test.sh docs/superpowers/plans/2026-07-11-sub-coordinator-worker-routing-separation.md
git commit -m "fix(run-with-it): separate worker routing"
```
