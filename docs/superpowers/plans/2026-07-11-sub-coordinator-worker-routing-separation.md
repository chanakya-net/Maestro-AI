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
- Modify: `tests/run-with-it-dispatch.test.sh`
- Modify: `tests/run-with-it-dispatch-ps1.test.sh`

**Interfaces:**
- Consumes: Existing static contract assertion helpers and `run-with-it-router.py` CLI.
- Produces: Regression assertions requiring canonical worker override names and forbidding coordinator-runtime leakage.

- [x] **Step 1: Add routing contract assertions**

Add assertions requiring the Step C environment block and override precedence section to use `FORCED_AGENT` and `FORCED_MODEL`. Add forbidden assertions for `AGENT=<value-if-set>`, `MODEL=<value-if-set>`, and the old ``AGENT` + `MODEL` forced together` wording.

- [x] **Step 2: Add the easy-route counterfactual**

Invoke `run-with-it-router.py` with a temporary empty ledger, `--role impl`, `--complexity-level easy`, and Codex-only agent constraints, but no forced model. Assert the selected model is not `gpt-5.6-sol` and the reason is not `forced-agent-and-model`.

- [x] **Step 3: Add pool contract assertions**

Require Bash and PowerShell pool tests to retain `gpt-5.6-sol` as the Sub-Coordinator default while asserting their documentation does not describe that value as a child override.

- [x] **Step 4: Run tests and verify RED**

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
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `assets/run-with-it-dispatch.ps1`
- Modify: `README.md`
- Modify: `tests/run-with-it-routing.test.sh`
- Modify: `tests/run-with-it-pool-ps1.test.sh`
- Modify: `tests/run-with-it-dispatch.test.sh`
- Modify: `tests/run-with-it-dispatch-ps1.test.sh`

**Interfaces:**
- Consumes: Canonical `FORCED_AGENT` and `FORCED_MODEL` router arguments already accepted by `run-with-it-router.py`.
- Produces: Context files containing canonical worker overrides only when explicitly requested.

- [x] **Step 1: Update the skill input contract**

Document `FORCED_AGENT` and `FORCED_MODEL` as canonical inputs. Mark `AGENT` and `MODEL` as deprecated aliases accepted only when explicitly named by the user, never read from ambient process state.

- [x] **Step 2: Update Step C context generation**

Replace `AGENT=<value-if-set>` and `MODEL=<value-if-set>` with `FORCED_AGENT=<explicit-worker-override-if-set>` and `FORCED_MODEL=<explicit-worker-override-if-set>`. Add an invariant forbidding `SUB_COORD_*` values from populating these fields.

- [x] **Step 3: Update orchestration and Sub-Coordinator rules**

State that the pool dispatcher’s `--agent` and `--model` values configure only the Sub-Coordinator process. Change override precedence to `FORCED_AGENT` and `FORCED_MODEL`; explicitly classify ambient `AGENT` and `MODEL` as runner telemetry, not routing policy.

- [x] **Step 4: Scrub legacy aliases at both dispatcher boundaries**

Unconditionally remove ambient `AGENT`, `MODEL`, and
`RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES` before Bash and PowerShell launch child
agents. Preserve explicitly supplied canonical `FORCED_AGENT` and
`FORCED_MODEL` values unchanged in foreground and detached dispatch.

- [x] **Step 5: Remove trust in the legacy provenance marker**

Delete the marker contract from the skill, README, Main Orchestrator rules, and
Sub-Coordinator prompt. Add Bash and PowerShell dispatcher regressions proving
that an ambient marker cannot promote legacy aliases, is scrubbed from the
child environment, and cannot override canonical `FORCED_*` values.

- [x] **Step 6: Update README documentation**

Mirror the canonical override names, compatibility note, and coordinator/worker separation in user-facing configuration documentation.

- [x] **Step 7: Run targeted tests and verify GREEN**

Run:

```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: all five scripts exit 0 and print their PASS lines.

### Task 3: Verify the full routing surface

**Files:**
- Verify: `assets/run-with-it-router.py`
- Verify: `assets/run-with-it-pool.sh`
- Verify: `assets/run-with-it-pool.ps1`
- Verify: `assets/run-with-it-dispatch.sh`
- Verify: `assets/run-with-it-dispatch.ps1`
- Verify: `tests/run-with-it-router.test.sh`
- Verify: `tests/run-with-it-routing.test.sh`
- Verify: `tests/run-with-it-pool.test.sh`
- Verify: `tests/run-with-it-pool-ps1.test.sh`
- Verify: `tests/run-with-it-dispatch.test.sh`
- Verify: `tests/run-with-it-dispatch-ps1.test.sh`
- Verify: all changed documentation and tests

**Interfaces:**
- Consumes: Completed contract changes from Tasks 1 and 2.
- Produces: Evidence that existing routing, pool, and dispatcher behavior remains valid.

- [x] **Step 1: Run focused router and dispatcher suites**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: all six scripts exit 0.

- [x] **Step 2: Run the full contract suite**

Run:

```bash
for test_file in tests/*.test.sh; do bash "$test_file"; done
```

Expected: every test script exits 0.

- [x] **Step 3: Check formatting and scope**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; changes are limited to routing-contract
documentation/tests and the Bash/PowerShell dispatcher implementations. The
router, Bash/PowerShell pools, agent registry, weights, complexity bands,
subscription targets, and recovery behavior remain unchanged.

- [x] **Step 4: Record the executed commit history**

The work was committed incrementally; there is no single aggregate implementation
commit to create. The executed bookkeeping is:

- Test commits: `6d91029 test: lock worker routing contract`,
  `1558ccc test: strengthen routing compatibility tests`, and
  `3c95ab7 test: lock pool defaults and static checks`.
- Implementation commits: `18afa2a fix(run-with-it): separate worker routing`,
  `57e7703 fix(run-with-it): scrub legacy aliases`, and
  `0410c63 fix(run-with-it): remove marker trust`.
- Plan commits: `2845b30 docs: add worker routing implementation plan` and
  `3e10ebc docs(plan): record routing task history`.

The implementation scope across `46bf72c..0410c63` includes 11 files: the
skill, README, Main Orchestrator/Sub-Coordinator contracts, three routing/pool
tests, both dispatcher implementations, and both dispatcher tests. The router,
pool implementations, and agent registry were deliberately unchanged.
