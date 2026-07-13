# Markdown Contract Consistency — Final Implementation Plan

> **For agentic workers:** execute Tasks 1–6 and 8 in order using the checkbox (`- [ ]`) steps. **Task 7 is approval-gated** — it contains quasi-functional corrections and must not run without separate, explicit user approval. If a plan-execution sub-skill is installed, use it; otherwise work task-by-task in this session. Task 1 is written test-first: its assertions FAIL until Tasks 2–6 land.

**Goal:** Remove every contradictory or drifted statement in `skills/**/SKILL.md` and `assets/*.md` found in the July 2026 doc review, preserving current runtime behavior exactly, then lock the corrected wording in with a documentation contract test so it cannot drift again.

**Architecture:** Executable validators, the dispatcher scripts, and the existing shell test suite are the behavioral source of truth. Markdown contracts are aligned *to them*, never the other way around. One new shell contract test (matching the repo's existing `tests/*-contract.test.sh` convention) enforces the corrected wording and twin-file synchronization. Anything that would change what an obedient agent *does* — even in a contradictory edge case — is quarantined in approval-gated Task 7.

**Tech stack:** Markdown, Bash 3.2-compatible shell test (macOS default bash), `grep -F`, Git. No new dependencies.

## Runtime ground truth (verified against source — re-verify before editing if in doubt)

| Contract | Current behavior to document | Source |
|---|---|---|
| Verified no-op | `no_op: true` + `verification.passed=true` is accepted as success without a new commit | `assets/run-with-it-artifacts.py:247-249` |
| Plan artifact | `slices` must be a non-empty list; `status="success"`; `complexity_level` must be a router band | `assets/run-with-it-artifacts.py:314-339` (`valid_plan_payload`) |
| Sub-Coordinator report outcomes | `completed \| failed-review \| merge_failed \| blocked` | Appendix C2 merge contract; `STATUS\|type=sub-coord-complete` enum in SKILL.md Appendix B |
| Orchestrator issue statuses | terminal set includes `failed-merge` (set after merge recovery fails); `merge_recovery` is non-terminal | `assets/run-with-it-state.py:22` (`TERMINAL_OUTCOMES`) |
| Stall threshold | Effective value is **300** (dispatch snippets pass `--stall-seconds 300` explicitly); **600** is only the dispatcher's env fallback when no flag is passed | `assets/run-with-it-dispatch.sh:29` vs snippet defaults in `assets/sub-coordinator-prompt.md` |
| Auto-fail stalled roles | Script default `complexity,impl,modify,plan`; stall-based compatibility path, bounded by wrapper heartbeats — **not** deadline-based, **not** impl/modify-only | `assets/run-with-it-dispatch.sh:39,234,711` |
| Hard limit | Default 7200s applies **only** to `complexity\|impl\|modify\|review`; other roles default to 0 (unbounded) unless explicitly set | `assets/run-with-it-dispatch.sh:31-136` |
| Review band bump | Router applies `REVIEW_BUMP` internally (like `PLAN_BUMP`); coordinator passes the implementation band un-bumped | `assets/run-with-it-router.py:22,36,325,327` |
| Iteration limit | Review–modify cap is hard-coded to 8 in the prompt; `MAX_ITERATIONS` is read by **no script** and is not an active override | grep of `assets/*.sh`, `assets/*.py` |
| Complexity worker | Spawned by default; only an explicit runtime `COMPLEXITY_LEVEL`/`COMPLEXITY_SCORE` override skips it; two consecutive failures fall back to `medium-hard` (`score=25`) | `assets/sub-coordinator-prompt.md` fallback chain + `fallback=medium-hard` STATUS line |

If a proposed edit conflicts with this table, stop and re-verify the script before editing. If the script disagrees with the table, the script wins.

## Global constraints

- Do not edit `assets/*.py`, `assets/*.sh`, `assets/*.ps1`, registry JSON, or runtime schemas in Tasks 1–6. The only new non-Markdown file is `tests/markdown-contract-consistency.test.sh`.
- Do not write to `~/.ai-skill-collections/assets` or any other external mirror. Mirror sync is a manual post-merge step (Task 6, last item).
- **Scope guard on verification policy:** the "fix failures caused by the change; record pre-existing/infrastructure failures with evidence" rule applies to **`assets/modifier-prompt.md` only** (resolving its internal Scope-vs-Verification conflict toward its own majority reading). Do **not** touch the implementer's out-of-scope-failures rule at `assets/prompt.md:119` — it intentionally says the opposite, is internally consistent, and changing it would be functional.
- Do not weaken verification anywhere: a worker merely *claiming* "no changes" is never a verified no-op — the no-op contract requires a valid result artifact with passing verification.
- Preserve the current cleanup deletion set and sequencing; preserve explicit complexity-override behavior; preserve worktree/routing/merge/state/GitHub/artifact ownership.
- Keep prompts self-contained. Do not extract shared wording into a new required runtime asset. Compaction-safe duplication stays; it gets sync banners and twin-sync assertions instead of removal.
- **Twin files (the real pairs):** `skills/run-with-it/SKILL.md` ⇄ `assets/main-orchestrator-rules.md`, and `assets/sub-coordinator-prompt.md` ⇄ `assets/coordinator-rules.md`. Every fix touching one copy edits its twin in the same commit. (`prompt.md`/`modifier-prompt.md` are siblings that share some sections, not compaction twins.)
- Grouped commits by contract area, not one commit per sentence.
- Keep the test Bash 3.2-compatible; use `grep -F`, not `rg`.

---

## Task 1: Documentation contract regression test (write first — must fail)

**Files:**
- Create: `tests/markdown-contract-consistency.test.sh`
- Read only: existing `tests/*-contract.test.sh` (house style), `assets/run-with-it-artifacts.py`, `tests/run-with-it-artifacts.test.sh`, `tests/run-with-it-plan.test.sh`

**Assertion design rules:**
- Anchor on short, stable ASCII tokens (`no_op`, `plan_conformance`, `failed-merge`, `medium-hard`, `complexity,impl,modify,plan`) — never full sentences a benign rewording would break.
- For absence assertions on strings containing backticks or en dashes, copy the exact bytes from the current file into the test when writing it. Review every match in context; never blind-replace.
- Tolerate Markdown formatting where an enum can legitimately appear spaced or compact — assert both forms where relevant.
- Twin-sync assertions require the same token in both twin files. Do **not** require `fallback=medium-hard` in both twins — coordinator-rules.md doesn't carry STATUS-line syntax; require the band token `medium-hard` in both and the STATUS token only in `sub-coordinator-prompt.md`.

- [ ] **Step 1: Harness** (repo house style, Bash 3.2-safe):

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
FAILURES=0

fail() { printf 'FAIL: %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }

assert_contains() {  # file token message
  grep -Fq -- "$2" "$ROOT_DIR/$1" || fail "$1: $3"
}

assert_not_contains() {  # file token message
  if grep -Fq -- "$2" "$ROOT_DIR/$1"; then fail "$1: $3"; fi
}

assert_twins_contain() {  # fileA fileB token message
  assert_contains "$1" "$3" "$4 (missing in $1)"
  assert_contains "$2" "$3" "$4 (missing in $2)"
}
```

End with:

```bash
if [ "$FAILURES" -gt 0 ]; then
  printf '%d markdown contract failure(s)\n' "$FAILURES" >&2
  exit 1
fi
printf 'markdown contract consistency: OK\n'
```

- [ ] **Step 2: Assertions for the verified no-op contract (Task 2):**

```bash
# Coordinator side accepts the verified no-op
assert_contains "assets/sub-coordinator-prompt.md" 'no_op' \
  "sub-coordinator must document no-op artifact acceptance"
assert_contains "assets/coordinator-rules.md" 'verified no-op' \
  "coordinator-rules must carve out the verified no-op exception"

# Worker prompts: every commit-only gate carries the no-op alternative
assert_contains "assets/prompt.md" 'or the verified no-op result artifact' \
  "implementer done-file gates must allow the verified no-op"
assert_contains "assets/modifier-prompt.md" 'or the verified no-op result artifact' \
  "modifier done-file gates must allow the verified no-op"
```

- [ ] **Step 3: Assertions for orchestration limits, outcomes, ownership (Task 3):**

```bash
# Iteration limit
assert_contains "skills/run-with-it/SKILL.md" 'hardcoded to 8 cycles' \
  "MAX_ITERATIONS row must state the 8-cycle cap is hardcoded and the variable inactive"

# Complexity fallback band — band token in both twins, STATUS token where it lives
assert_not_contains "assets/coordinator-rules.md" 'default to medium and continue' \
  "complexity fallback must be medium-hard, not medium"
assert_contains "assets/coordinator-rules.md" 'medium-hard' \
  "coordinator-rules must state the medium-hard fallback"
assert_contains "assets/sub-coordinator-prompt.md" 'fallback=medium-hard' \
  "sub-coordinator STATUS contract keeps fallback=medium-hard"

# Stall threshold description matches effective runtime value
assert_contains "assets/coordinator-rules.md" 'WORKER_STALL_SECONDS=300' \
  "coordinator-rules must document the effective 300s snippet value"

# Terminal sets — two levels, kept distinct:
# orchestrator issue statuses include failed-merge...
assert_twins_contain "skills/run-with-it/SKILL.md" "assets/main-orchestrator-rules.md" \
  'completed / failed-review / failed-merge / blocked' \
  "orchestrator terminal enumerations must include failed-merge"
assert_not_contains "skills/run-with-it/SKILL.md" 'completed / failed-review / blocked' \
  "stale three-status orchestrator enumeration must be gone"
assert_not_contains "assets/main-orchestrator-rules.md" 'completed / failed-review / blocked' \
  "stale three-status orchestrator enumeration must be gone"
# ...while sub-coordinator REPORT outcomes use merge_failed, never failed-merge
assert_contains "assets/sub-coordinator-prompt.md" 'completed | failed-review | merge_failed | blocked' \
  "Appendix E outcome enum must include merge_failed"
assert_not_contains "assets/sub-coordinator-prompt.md" 'failed-merge' \
  "failed-merge is an orchestrator issue status and must not leak into sub-coordinator outcomes"

# Auto-fail role list matches the script default in all three docs
assert_contains "assets/sub-coordinator-prompt.md" 'complexity,impl,modify,plan' \
  "auto-fail role default must match run-with-it-dispatch.sh"
assert_twins_contain "skills/run-with-it/SKILL.md" "assets/coordinator-rules.md" \
  'complexity,impl,modify,plan' "auto-fail role default must match in twins"

# Worker-watch ownership
assert_contains "assets/main-orchestrator-rules.md" 'never runs worker-watch itself' \
  "Main Orchestrator must not be told to run worker-watch directly"
```

- [ ] **Step 4: Assertions for schemas and single-file contradictions (Tasks 4–5):**

```bash
# Plan template must not emit an empty slices array (valid_plan_payload rejects it)
assert_not_contains "assets/plan-prompt.md" '"slices": [],' \
  "Bash plan example must not produce an invalid empty slices array"
assert_not_contains "assets/plan-prompt.md" 'slices = @()' \
  "PowerShell plan example must not produce an invalid empty slices array"

# Review schema includes every required coverage row
assert_contains "assets/review-prompt.md" 'plan_conformance | maintainability' \
  "review schema area enum must include plan_conformance"

# Complexity prompt acceptance checks describe the file truthfully
assert_not_contains "assets/complexity-prompt.md" 'Contains CodeGraph tool instructions' \
  "acceptance check must not claim CodeGraph instructions exist"

# Merge-recovery bootstrap copy-paste leftovers removed
assert_not_contains "assets/merge-recovery-prompt.md" 'tdd-implementation' \
  "merge recovery never bootstraps tdd-implementation"
assert_not_contains "assets/merge-recovery-prompt.md" 'both activations' \
  "merge recovery bootstraps a single skill"

# Modifier verification scoped to change-caused failures
assert_contains "assets/modifier-prompt.md" 'caused by the reviewed change before reporting completion' \
  "modifier verification must be scoped to change-caused failures"

# Reviewer bump owned by the router; manual table scoped to fallback
assert_contains "assets/sub-coordinator-prompt.md" 'REVIEW_BUMP' \
  "route-helper inputs must name the router's internal review bump"
assert_contains "assets/sub-coordinator-prompt.md" 'prompt fallback router only' \
  "manual reviewer band table must be scoped to the fallback router"

# Review-skip keys documented
assert_contains "assets/sub-coordinator-prompt.md" 'review_skipped' \
  "Appendix E schema must include the review_skipped key"
assert_contains "skills/run-with-it/SKILL.md" 'Review: skipped' \
  "Appendix D must define the terminal-comment line for skipped review"

# Skill isolation permits the governing-prompt bootstrap
assert_contains "skills/save-tokens/SKILL.md" 'governing prompt' \
  "save-tokens isolation must allow the worker-prompt bootstrap"
assert_contains "skills/tdd-implementation/SKILL.md" 'governing prompt' \
  "tdd-implementation isolation must allow the worker-prompt bootstrap"

# No-Git support claims scoped to what actually works without git
assert_contains "skills/run-with-it/SKILL.md" 'asset discovery and local-issue intake' \
  "no-git support claim must be scoped; branches/worktrees/merges require git"
```

- [ ] **Step 5: Assertions for stale references and typos (Task 6):**

```bash
assert_not_contains "skills/run-with-it/SKILL.md" 'Preflight Check 14' \
  "stale preflight cross-reference must be corrected"
assert_not_contains "skills/create-git-issue/SKILL.md" 'outsise' "typo"
assert_not_contains "skills/create-git-issue/SKILL.md" 'requirment in detils' "typo"
```

For absence checks whose old strings contain backticks/en dashes — "(Bash default: `complexity`)" and the review-gate "`files_changed` 2–4" phrase (the latter only if Task 7B is approved) — copy exact bytes from the current files when writing the test.

- [ ] **Step 6:** `chmod +x` the test; run `bash tests/markdown-contract-consistency.test.sh`; confirm it FAILS with failures corresponding to Tasks 2–6. Commit:

```bash
git add tests/markdown-contract-consistency.test.sh
git commit -m "test: codify markdown documentation contracts"
```

---

## Task 2: Verified no-op — align end to end

**Files:** `assets/prompt.md`, `assets/modifier-prompt.md`, `assets/sub-coordinator-prompt.md`, `assets/coordinator-rules.md`

- [ ] **2.1 Worker prompts — fix every commit-only gate, not just the main paragraph.** Both files already define the verified no-op variant but three later gates contradict it. Add the alternative "(or the verified no-op result artifact is written per the Verified no-op variant)" to each:
  - `assets/prompt.md:163` ("Do not write the done file until the commit is made…"), `:270` (Completion Sentinel), `:284` (final done-file guard), `:288` (Output Contract "Do not output this report until… the mandatory commit is made").
  - `assets/modifier-prompt.md:161`, `:262`, `:276`, `:293` (the mirrored gates).
  One consistent rule everywhere: *success requires either a new commit or an explicitly reported, verified no-op (`no_op: true` with passing verification); a bare "no changes" claim is a failure.* Do not weaken verification.
- [ ] **2.2 Coordinator side — accept the verified no-op.**
  - `assets/sub-coordinator-prompt.md:965` (impl no-commit check): add *"Exception: if the worker's result artifact is valid with `"no_op": true` and `verification.passed=true` (the verified no-op contract in `prompt.md`), treat the phase as success with `commit_sha=NONE`; do not mark `implementer-no-commit`."*
  - `assets/sub-coordinator-prompt.md:1215` (modifier no-commit check): mirror it, referencing `modifier-prompt.md`.
  - `assets/sub-coordinator-prompt.md:1499` (Worker Done Files impl/modify validity): "…and either the worker's mandatory commit was made in the issue worktree (captured SHA differs from the pre-spawn baseline and matches the issue worktree `HEAD`) **or** the result is a verified no-op (`no_op: true` with `verification.passed=true`), validated by `run-with-it-artifacts.py`."
  - `assets/coordinator-rules.md:90` (twin): "…as `failed-review` **unless the result is a verified no-op** (`no_op: true`, passing verification), which is success."
- [ ] **2.3** Run the contract test (no-op assertions green; others may stay red). Commit:

```bash
git add assets/prompt.md assets/modifier-prompt.md assets/sub-coordinator-prompt.md assets/coordinator-rules.md
git commit -m "docs: align verified no-op handoffs end to end"
```

*Why behavior-neutral:* documents the acceptance path `run-with-it-artifacts.py` already implements; genuine missing-commit failures (no valid artifact, or `no_op` absent/failing) unchanged.

---

## Task 3: Orchestration limits, outcomes, timing, ownership

**Files:** `skills/run-with-it/SKILL.md`, `assets/main-orchestrator-rules.md`, `assets/sub-coordinator-prompt.md`, `assets/coordinator-rules.md`

- [ ] **3.1 Iteration limit.** `skills/run-with-it/SKILL.md:140`: change the `MAX_ITERATIONS` description to *"Deprecated / no effect. The review–modify loop cap is hardcoded to 8 cycles in `sub-coordinator-prompt.md` (Appendix B). Still forwarded in context files for backward compatibility but not consulted."* Keep default `20` in the table and keep forwarding it in Step C (context files stay byte-identical). Do not touch the `8` in the prompt.
- [ ] **3.2 Complexity fallback band.** `assets/coordinator-rules.md:41`: "default to medium and continue" → "default to `medium-hard` (`score=25`) and continue", matching `sub-coordinator-prompt.md:666` and its `fallback=medium-hard` STATUS line. Keep the explicit-override skip behavior exactly as documented.
- [ ] **3.3 Stall threshold description.** `assets/coordinator-rules.md:85` → *"The default output thresholds are `WORKER_QUIET_SECONDS=120` and `WORKER_STALL_SECONDS=300` (the value the dispatch snippets pass explicitly; the dispatcher's env fallback when no flag is passed is 600). Wrapper heartbeat defaults to 30 seconds; the hard limit defaults to 7200 seconds for complexity/impl/modify/review roles and is unbounded (0) for other roles unless explicitly set."* Snippets in `sub-coordinator-prompt.md` unchanged.
- [ ] **3.4 Auto-fail stalled roles.** `assets/sub-coordinator-prompt.md:1482`: "(Bash default: `complexity`)" → "(Bash default: `complexity,impl,modify,plan`)", plus: *"Auto-fail applies only when the runner emits no current wrapper heartbeat; a heartbeat-alive worker is never terminated for quiet stdout alone — `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` is the elapsed-time bound."* Add the same role list + gating sentence to `SKILL.md:627` and `coordinator-rules.md:73`, keeping their "compatibility fallback" framing. Verify the gating sentence against `run-with-it-dispatch.sh:234,711,732` while editing; adjust the wording to the script, never the script.
- [ ] **3.5 Terminal sets — two levels, kept distinct.**
  - *Orchestrator issue statuses:* everywhere "completed / failed-review / blocked" appears as the terminal set (`SKILL.md:26, 356-357, 401, 581`; `main-orchestrator-rules.md:55, 89`) → "completed / failed-review / failed-merge / blocked". In SKILL.md Appendix D (`:927`), add `failed-merge` to the terminal-comment outcome list and the template's `## Status` values. `merge_recovery` stays non-terminal.
  - *Sub-Coordinator report outcomes:* `sub-coordinator-prompt.md` Appendix E — the intro line (`:1506`) and the `"outcome"` enum (`:1516`) currently omit `merge_failed` even though Appendix C2 requires writing `outcome="merge_failed"` on merge failure. Extend both to `completed | failed-review | merge_failed | blocked`. Do **not** add `failed-merge` here — it is the orchestrator-level status assigned after merge recovery, never a report outcome.
- [ ] **3.6 Worker-watch ownership.** `assets/main-orchestrator-rules.md:49` → *"Sub-Coordinator liveness is monitored by the platform pool runner, which invokes `worker-watch.sh` / `worker-watch.ps1` inside the dispatcher; the Main Orchestrator only loops the bounded watch runner and never runs worker-watch itself."* Soften line 46 so the bounded watch runner is the standard mechanism and `current.txt` is an on-demand terminal view. One component owns each watcher action; others are observers.
- [ ] **3.7** Run the contract test; commit:

```bash
git add skills/run-with-it/SKILL.md assets/main-orchestrator-rules.md assets/sub-coordinator-prompt.md assets/coordinator-rules.md
git commit -m "docs: align orchestration limits, outcomes, and ownership"
```

---

## Task 4: Schemas and single-file contradictions

**Files:** `assets/plan-prompt.md`, `assets/review-prompt.md`, `assets/complexity-prompt.md`, `assets/merge-recovery-prompt.md`, `assets/modifier-prompt.md`, `assets/sub-coordinator-prompt.md`, `skills/run-with-it/SKILL.md`

- [ ] **4.1 Plan template empty-slices examples.** In `assets/plan-prompt.md`, replace the Bash example's `"slices": [],` and PowerShell `slices = @()` with one clearly-placeholder entry:
  ```json
  "slices": [
    { "order": 1, "behavior": "REPLACE_WITH_SLICE_BEHAVIOR", "test_target": "REPLACE_WITH_TEST_TARGET", "files": [] }
  ],
  ```
  (PowerShell: `slices = @(@{ order = 1; behavior = "REPLACE_WITH_SLICE_BEHAVIOR"; test_target = "REPLACE_WITH_TEST_TARGET"; files = @() })`.) A verbatim-copied template now passes `valid_plan_payload` instead of failing as `invalid-plan-result-artifact`. `files`/`interfaces`/`risks`/`out_of_scope` may stay empty (validator allows it).
- [ ] **4.2 `plan_conformance` in the reviewer schema.** `assets/review-prompt.md:156`: extend the `area` enum to `"requirements | correctness | security | tests | scope | plan_conformance | maintainability"`. Line 102 unchanged.
- [ ] **4.3 Complexity-prompt acceptance check.** `assets/complexity-prompt.md:222`: "Contains CodeGraph tool instructions and grep/find fallback" → "Contains read-only file-discovery instructions (grep/find/cat/Read)". Remove the false check; do **not** add CodeGraph capability.
- [ ] **4.4 Merge-recovery copy remnants.** `assets/merge-recovery-prompt.md:9-16`: "until both activations complete" → "until the activation completes"; delete the "Follow test-first discipline as `tdd-implementation` intends" fallback bullet. Keep the `save-tokens` fallback and the `skill-tool-unavailable-fallback` note.
- [ ] **4.5 Modifier scope-vs-verification conflict (modifier only).** `assets/modifier-prompt.md:284`: → *"Fix any failing test caused by the reviewed change before reporting completion, regardless of where in the tree the failure surfaces. For failures you can demonstrate are pre-existing or infrastructure-caused, record the concrete evidence per the Scope rules instead of broadening the patch."* Align the closing line to "A failing test suite caused by this change is a failed modification." **Do not touch `assets/prompt.md:119`.**
- [ ] **4.6 Reviewer band bump ownership.** `assets/sub-coordinator-prompt.md:541`: append *"the router's `REVIEW_BUMP` applies the one-band increase internally, so pass the implementation band as-is — never pre-bump it here."* Retitle "Reviewer Band Selection" (`:975`) to "Reviewer Band Selection (prompt fallback router only)" with a lead-in scoping it to when `run-with-it-router.py` is unavailable.
- [ ] **4.7 Review-skip report keys.** `assets/sub-coordinator-prompt.md` Appendix E: add optional keys `"review_skipped": false` / `"review_skip_reason": null` ("set only when Step 0 skipped review"), noting `reviewer_model` is `null` and `final_verdict` `"approve"` on skip. `skills/run-with-it/SKILL.md:970`: "…when `DELEGATED_REVIEW=true` and review ran; when `report.review_skipped` is true, write `Review: skipped (trivial-change)` instead."
- [ ] **4.8 No-Git claims scoped.** `skills/run-with-it/SKILL.md:250` (Fresh/No-Git Project Notes): scope the claim — *"Without git, this skill supports asset discovery and local-issue intake only; issue branches, worktrees, merges, merge recovery, and the final PR require a git repository."* Keep "asset discovery is filesystem-based" and "if git metadata is unavailable, continue with empty commit context" as they are.
- [ ] **4.9** Run the contract test; commit:

```bash
git add assets/plan-prompt.md assets/review-prompt.md assets/complexity-prompt.md assets/merge-recovery-prompt.md assets/modifier-prompt.md assets/sub-coordinator-prompt.md skills/run-with-it/SKILL.md
git commit -m "docs: repair prompt schemas and single-file contradictions"
```

---

## Task 5: Skill isolation — minimal widening (no restructuring)

**Files:** `skills/save-tokens/SKILL.md`, `skills/tdd-implementation/SKILL.md`, `skills/break-req/SKILL.md`, `skills/help-me-debug/SKILL.md`, `skills/create-git-issue/SKILL.md`

- [ ] **5.1** In each Skill Isolation block (all identical today), widen only the exception clause: *"unless explicitly called by name via a `Skill` tool call — whether from this skill's own workflow or from the governing prompt/skill that activated this one (e.g. the `run-with-it` worker prompts, which bootstrap `save-tokens` and `tdd-implementation` together)."* This resolves the contradiction where two skills each claim sole authority while worker prompts mandate activating both. **Do not** restructure the blocks into context/artifact/failure contracts — that changes their meaning (they govern skill-activation exclusivity, nothing else). Suppression of spontaneous activations stays.
- [ ] **5.2** Run the contract test; commit:

```bash
git add skills/save-tokens/SKILL.md skills/tdd-implementation/SKILL.md skills/break-req/SKILL.md skills/help-me-debug/SKILL.md skills/create-git-issue/SKILL.md
git commit -m "docs: allow governing-prompt skill bootstrap in isolation blocks"
```

---

## Task 6: Repetition de-drift, minor defects, mirror note

**Files:** the four twin files, `assets/prompt.md`, `assets/modifier-prompt.md`, `skills/run-with-it/SKILL.md`, `skills/create-git-issue/SKILL.md`

- [ ] **6.1 Sync banners.** Add to each intentionally-duplicated block in the four twinned documents:
  `<!-- SYNC: intentionally duplicated in <twin file>; the repository copy is authoritative over any installed mirror. Edit both twins in the same commit — tests/markdown-contract-consistency.test.sh asserts key tokens match. -->`
- [ ] **6.2 Collapse same-audience duplication only** (keep cross-session duplication — isolated workers can't follow pointers):
  - "Worker result files must never be `report.json`": full statement stays in `coordinator-rules.md:58` and both worker prompts; `SKILL.md:598` and `sub-coordinator-prompt.md:386` shrink to one sentence + pointer.
  - "Assemble contexts for ALL pending issues up front" rationale: full rationale once in SKILL.md Step B; the `SKILL.md:25` bullet and `main-orchestrator-rules.md:39` keep the rule + pointer.
  - Sticky-reviewer rule: normative copy at `sub-coordinator-prompt.md:541`; rule 5 at `:993` becomes a pointer (reinforces 4.6).
  - `sub-state.json` double bootstrap: merge "Mandatory State Bootstrap" (`:205-224`) into "Issue Worktree Bootstrap"; keep the field-requirements paragraph, delete the divergent minimal-schema example, note the `:121` snippet as the canonical initial write.
  - Events-log tension: qualify `coordinator-rules.md:27` — append to `$RUN_WITH_IT_EVENTS_LOG` "for lines you emit yourself (the dispatcher/runner already appends its own — see the no-double-logging rule below)".
  - **Leave duplicated** with sync banners only: "Code Size & Maintainability" (prompt.md ⇄ modifier-prompt.md) and the OS-detection table (SKILL.md ⇄ sub-coordinator-prompt.md).
- [ ] **6.3 Minor defects:**
  - `skills/run-with-it/SKILL.md:883`: "per Preflight Check 14" → "per Preflight Check 6 (existing-state detection)".
  - `skills/create-git-issue/SKILL.md:79,221`: "outsise sandbox" → "outside sandbox"; "Try to capture requirment in detils" → "Capture requirements in detail."
  - `assets/prompt.md:22`: "Implement only the issue(s) assigned" → "Implement only the single issue assigned".
  - `skills/run-with-it/SKILL.md:658` (`--unattended` row): "Yes — always pass in run-with-it dispatches (required whenever a permission mode is set)."
- [ ] **6.4 Mirror (manual, post-merge — no writes during this plan):** after the PR merges, the user re-runs the one-command asset sync from SKILL.md so `~/.ai-skill-collections/assets` gains `run-with-it-stop.ps1` / `run-with-it-watch.ps1` and picks up the corrected Markdown. Leave the orphan `run-with-it-events.py` in the mirror.
- [ ] **6.5** Run the contract test — **all Task 1 assertions must now pass.** Commit:

```bash
git add assets skills
git commit -m "docs: remove markdown drift, repetition, and stale references"
```

---

## Task 7: Quasi-functional corrections — SEPARATE APPROVAL REQUIRED

Do not execute as part of the behavior-neutral pass. Both items change what an obedient agent *does* in an edge case, even though both live in Markdown (these behaviors are prompt-enforced; there is no shell/Python "review gate" or baseline-capture code to modify).

### Option A: Anchor the pre-implementation baseline confirm to the issue worktree

- **File:** `assets/sub-coordinator-prompt.md:871-877`.
- **Today:** the literal snippet `ISSUE_BASE_SHA=$(git rev-parse HEAD)` re-captures (and overwrites) the bootstrap value from whatever directory the coordinator happens to be in — the root checkout's HEAD can differ from the issue worktree's.
- **Proposed:** `ISSUE_BASE_SHA="${ISSUE_BASE_SHA:-$(git -C "$ISSUE_WORKTREE_PATH" rev-parse HEAD)}"` and reword "Capture" → "Confirm the issue baseline SHA (captured at worktree bootstrap)".
- **Why gated:** on the correct path the value is identical (plan phase is read-only), but where the old snippet *was* misreading root HEAD, this changes the reviewer's diff base — a bug fix, not a neutral edit.
- **On approval:** apply, add a contract-test assertion for the token `ISSUE_BASE_SHA:-$(git -C "$ISSUE_WORKTREE_PATH"`, commit separately: `git commit -m "fix(docs): anchor issue baseline confirm to the issue worktree"`.

### Option B: Define review-skip gate precedence (resolve the overlapping table)

- **File:** `assets/sub-coordinator-prompt.md:1046-1052`.
- **Today:** rows overlap — `files=4` matches both "mandatory" and "gray zone"; `files 2–3 & lines<30` matches both "skip" and "gray zone". Behavior in the overlap is undefined, so any resolution defines it.
- **Proposed precedence (evaluated in order):**
  1. `files_changed > 3` OR `total_lines_changed > 55` → review mandatory.
  2. `files_changed ≤ 3` AND `total_lines_changed < 30` AND explicit all-tests-pass → skip.
  3. Otherwise → review required unless verification shows 100% explicit all-tests-pass, then skip.
  Drop the "`files_changed` 2–4" phrase (the source of the overlap).
- **Boundary matrix (document in the section on approval):**

  | Files | Lines | Tests pass | Expected |
  |---:|---:|---|---|
  | 3 | 29 | yes | skip (rule 2) |
  | 3 | 29 | no | review (rule 3) |
  | 4 | 29 | yes | review (rule 1) |
  | 3 | 30 | yes | skip (rule 3) |
  | 3 | 55 | yes | skip (rule 3) |
  | 3 | 56 | yes | review (rule 1) |
  | 4 | 55 | yes | review (rule 1) |

- **On approval:** apply, add an exact-bytes `assert_not_contains` for the old gray-zone phrase, commit separately: `git commit -m "fix(docs): define review-skip gate precedence"`.

---

## Task 8: Full verification and handoff

- [ ] **8.1** `bash tests/markdown-contract-consistency.test.sh` → passes.
- [ ] **8.2** Full suite:

```bash
for test_file in tests/*.test.sh; do
  bash "$test_file" || { echo "SUITE FAIL: $test_file"; exit 1; }
done
```

Expected: every test passes (nothing the existing tests cover was changed).
- [ ] **8.3** Patch hygiene: `git diff --check`; `git status --short` — no unrelated or user-owned files staged.
- [ ] **8.4** Prove the default path touched no runtime files: the changed-file list contains only `.md` files under `assets/` and `skills/`, `docs/`, plus `tests/markdown-contract-consistency.test.sh`. Any `.sh`/`.ps1`/`.py`/JSON change without Task 7 approval is a failure — and Task 7 itself touches only Markdown.
- [ ] **8.5** Stale-contract scan (review each match in context, never blind-replace): obsolete terminal enums (spaced and compact), commit-only success wording, active-`MAX_ITERATIONS` claims, "Bash default: `complexity`)", "Preflight Check 14", unfinished placeholder text.
- [ ] **8.6** Manual consistency review: twins agree where contracts overlap; role-specific differences remain intentional (implementer vs modifier test policy); removed repetition wasn't needed by an isolated worker session; cleanup and implementer behavior unchanged; Task 7 edits absent unless approved.

---

## Expected default change set

- One new Markdown contract shell test.
- Behavior-neutral edits to `skills/**/SKILL.md` and `assets/*.md` only.
- No runtime implementation changes; no external mirror writes.
- Passing focused and full test suites.
- Grouped, reviewable commits per contract area.
- Task 7 remains a separate proposal until explicitly approved.
