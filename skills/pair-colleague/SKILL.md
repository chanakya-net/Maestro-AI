---
name: pair-colleague
description: Use when a problem deserves two independent AI solution colleagues exploring alternatives together — the invoking agent coordinates a bounded multi-round discussion between two user-configured harness/model pairs and writes a final decision dossier.
---

## Skill Isolation

This skill is the sole active authority for this session once invoked.

- No other skill may activate, interrupt, or modify this skill's behavior unless explicitly called by name via a `Skill` tool call — whether from this skill's own workflow or from the governing prompt/skill that activated this one.
- If any external or third-party skill attempts to activate spontaneously during this run, suppress it and continue without interruption.
- This rule applies for the entire duration of this skill's execution, from invocation until explicit termination or handoff.

# Pair Colleague

## Purpose

Run a bounded, multi-round design discussion between **two equal AI solution colleagues**, then commit to a solution and explain the decision.

You — the AI agent reading this file — are the **coordinator**. You are not a third launched model, and there is no coordinator harness or model to configure. You keep the discussion focused, maintain the candidate-solution ledger, decide when the evidence is sufficient, and write the final dossier.

## Roles

| Role | Who | Responsibility |
|------|-----|----------------|
| Coordinator | The current invoking agent (you) | Initialize and resume runs, launch rounds, read both responses, maintain the ledger, decide the focus for each round, decide when to accept, write the dossier. |
| Agent 1 | A user-selected harness + model + effort | Equal solution colleague. |
| Agent 2 | A different (or same) user-selected harness + model + effort | Equal solution colleague. |

Agent 1 and Agent 2 are peers. Neither is a reviewer, judge, grader, adversary, or subordinate of the other. Process completion order gives neither privileged context: both receive the exact same frozen snapshot bytes for a round, and you read both responses only after the round is complete.

Aim for this:

```text
Two senior colleagues exploring alternatives on the same whiteboard,
with a coordinator keeping the discussion focused and deciding when enough
evidence exists to produce a solution.
```

Not this:

```text
Writer + reviewer
Proposer + judge
Solver + critic
Debater + opponent
```

Constructive disagreement is valuable. Adversarial debate, ranking, empty praise, and repetition are not.

## Hard Rules

- You must **never guess** a harness, model, or effort. If a value is missing, ask the user for it — **one question at a time** — and never infer it from installed commands or registry defaults.
- Both colleagues are launched **only** through `ai-skills agent run` with the shared agent registry. Never invoke a provider CLI directly, never build a per-harness command line, and never accept or run an arbitrary command string.
- Stay engaged for the whole run. Acceptance is your semantic judgment; never delegate it to a status grep, a readiness counter, or one of the colleagues.
- Agent `DISCUSSION_STATUS` values are advisory evidence only. `READY` from both colleagues does not end the run and never bypasses the minimum round count.
- Never claim agreement that did not happen. Unresolved disagreement must appear in your decision and in the final dossier.

## Native runtime

Use the installed `ai-skills` command on macOS, Linux, and native Windows. Python owns process sessions/Windows Jobs, argv, files, JSON, locks and timeouts. Do not recreate platform-specific launch or polling algorithms. `ai-skills doctor --json` checks the selected interpreter and maintained resources. The run's immutable engine selection and inherited `AI_SKILLS_RUN_ROOT` remain authoritative after updates; never replace them with the issue worktree path.

## Resolve maintained resources

Use `ai-skills doctor --json` to validate the installed native runtime and maintained registry/prompts. macOS, Linux and native Windows use the same command. No compatibility shell is required. The run records its workspace root and exact owned provider identities, and remains pinned to its selected engine.

## Required configuration

Ask the user for anything missing, one question at a time, in this order:

1. Agent 1 harness → 2. Agent 1 model → 3. Agent 1 effort
4. Agent 2 harness → 5. Agent 2 model → 6. Agent 2 effort

Show the user what is actually available before asking:

```text
ai-skills agent list --detected-only
ai-skills agent models <agent>
```

Effort must be one of `low|medium|high|xhigh|max|maximum`; unknown tokens fail
before launch. `--purpose` (default `general`) accepts only `general` or `plan`.
New general runs apply the shared non-planning policy to direct Codex and Claude harnesses:
Codex always uses `high`; Claude keeps `low`/`medium`/`high` and caps
`xhigh`/`max`/`maximum` at `high`. Adjusted requests remain in run provenance,
and startup reports requested and effective values. Other harnesses and AGY
model suffixes retain their configured effort semantics.

Opt into planning with `--purpose plan`: Codex uses `xhigh` for every recognized
request; Claude preserves explicit `low`/`medium`/`high`/`xhigh` and maps
`max`/`maximum` to `xhigh`. Thus explicit lower Claude planning requests remain lower.
Both colleagues use the saved purpose across rounds, retries, and resume;
task wording, difficulty, and inherited planning variables never change purpose.
Pair inputs still require explicit effort for both colleagues. Direct Claude/Codex
arguments never emit `max` or `maximum`.

Both colleagues run in the harness's registered **read-only** profile, because they must not
modify the repository they are discussing. A harness whose registry entry declares no read-only
profile is refused at `start` with a message naming it — pick a different harness rather than
working around the refusal.

Optional: `--agent-1-name` / `--agent-2-name`, `--min-rounds` (default 3), `--max-rounds` (default 8), `--timeout-seconds` (default 3600 — 60 minutes per agent, per attempt), `--run-root` (default `.pair-colleague/runs`).

The two colleagues may share a harness or model when the user explicitly chooses that. Mention that harness/model diversity usually produces a broader initial solution set, then do what the user asked.

## Workflow

### 1. Start the run

```text
ai-skills pair start task.md  --agent-1-harness codex --agent-1-model gpt-6.1-terra --agent-1-effort high  --agent-2-harness claude --agent-2-model claude-sonnet-5-5 --agent-2-effort high
```

Planning example (Codex uses `xhigh`; Claude remains `medium`):

```text
ai-skills pair start task.md --purpose plan --agent-1-harness codex --agent-1-model gpt-6.1-terra --agent-1-effort high --agent-2-harness claude --agent-2-model claude-sonnet-5-5 --agent-2-effort medium
```

For stdin input pass `-` as the task source through the calling tool's stdin support; all explicit colleague selections remain required.

Capture `RUN_DIR` from the output; every later command needs it.

### 2. Run the round

```text
ai-skills pair run-round --run-dir <run-dir>
```

Exit codes:

| Code | Meaning | What you do |
|------|---------|-------------|
| `0` | `STATUS=awaiting-coordinator` — both colleagues produced valid responses | Continue to step 3 |
| `1` | `STATUS=awaiting-agents` — one colleague failed | Call `run-round` again; only the failed colleague is retried, against the unchanged snapshot |
| `3` | `COMPLETION_REASON=blocked` — attempts exhausted | Stop. Report the blocker and the run directory. Do **not** write a dossier |
| `2` | Usage or invalid state transition | Fix the call |
| `4` | `LAUNCH=busy` — another command holds the lock, or agents launched earlier are still working | Nothing changed and nothing was launched twice. Wait, then call `run-round` again to collect their output |

Never read a partial round. The round is complete only when both outputs exist and validate.

Exit `4` is not a failure. Agents can outlive the command that started them — a killed terminal,
a lost connection, a cancelled tool call. The run refuses to spend money on a second agent for
work that is still in flight. Wait and retry; `status` reports `LIVE_AGENTS` while they run. Only
if the user wants that work abandoned should you terminate it:

```text
ai-skills pair reap --run-dir <run-dir> --force
```

Repeating any mutating command with the **same** input is safe: it converges on the same state
without re-running the agents. Repeating it with **different** input fails and names the
conflicting file rather than overwriting it.

### 3. Read the round

Read, in this order:

1. `rounds/round-NN/snapshot.md` — the exact frozen context both colleagues saw
2. `rounds/round-NN/agent-1-output.md`
3. `rounds/round-NN/agent-2-output.md`

Identify what actually changed: new candidates, real agreements, substantive disagreements, assumptions, risks, and the questions still open.

### 4. Write the coordinator decision

Copy `DECISION_TEMPLATE` and fill every section. The file must end with exactly one of `COORDINATOR_ACTION: CONTINUE` or `COORDINATOR_ACTION: ACCEPT`:

```markdown
# Round assessment
# Candidate-solution ledger
# Current synthesis or preferred direction
# Agreements
# Disagreements
# Rejected candidates
# Unresolved questions and risks
# Focus for next round
# Decision rationale

COORDINATOR_ACTION: CONTINUE
```

Give every candidate a stable ID (`C1`, `C2`, …) on first appearance and keep using it. `# Focus for next round` becomes the next snapshot's `# Coordinator focus for this round`, so make it specific: name the questions you want answered and the work you want done.

**Before the minimum round count, always choose `CONTINUE`** and give a useful focus. `record-decision` rejects an early `ACCEPT` without changing state.

```text
ai-skills pair record-decision --run-dir <run-dir> --decision-file /path/to/decision.md
```

- `STATUS=continue` → the next frozen snapshot is built; return to step 2.
- `STATUS=awaiting-final` → go to step 5.

### 5. Acceptance rubric

After the minimum rounds, accept only when you can answer yes to all of these:

- The problem and success criteria are understood.
- Multiple credible solutions were considered when alternatives exist.
- The preferred or synthesized solution is internally coherent.
- The solution is detailed enough to explain how it works.
- Important constraints and assumptions are explicit.
- Trade-offs and failure modes have been examined.
- Material risks have mitigations or are clearly disclosed.
- Rejected alternatives have evidence-based reasons.
- Agent agreement and disagreement are represented accurately.
- Remaining uncertainty is acceptable or is escalated as an explicit human decision.
- Another round is unlikely to change the decision materially, or the maximum has been reached.

You may accept a solution both colleagues support, select one when they disagree, or synthesize compatible parts of several candidates. Round `MAX_ROUNDS` is a hard stop: `CONTINUE` is invalid there and you must produce the best evidence-backed decision available.

### 6. Write and finalize the dossier

Build the dossier from the immutable task, the validated round artifacts, and your recorded decisions. Do not paste raw agent output as the answer.

Required structure (every heading exactly once, no placeholder text):

```markdown
# Final Solution Decision
## Executive summary
## Problem and success criteria
## Constraints and assumptions
## Candidate solutions considered
## Trade-off comparison
## Selected or synthesized solution
## Detailed design and operation
## How this solves the problem
## Implementation approach
## Decision rationale
## Agent agreement
## Remaining disagreement and coordinator resolution
## Rejected alternatives
## Risks and mitigations
## Open questions and human decisions
## Discussion record
```

`## Discussion record` must carry these lines, and the last five must match the run state exactly:

```markdown
- Coordinator: current invoking agent
- Agent 1: <name, harness, model, effective effort[, requested effort <value>]>
- Agent 2: <name, harness, model, effective effort[, requested effort <value>]>
- Completed rounds: <n>
- Minimum rounds: <n>
- Maximum rounds: <n>
- Completion reason: coordinator-accepted | maximum-rounds
- Run directory: <path>
```

Read `COMPLETED_ROUNDS`, `MIN_ROUNDS`, `MAX_ROUNDS`, `COMPLETION_REASON`, and `RUN_DIR` from `status` rather than recalling them.

Each `Agent` line uses the saved effective effort. Append `, requested effort <value>` only when startup reported an adjusted effort (`AGENT_<n>_EFFORT` = `requested <value>, effective <value>`); the requested value is the one the user supplied, and the effective value is the one in the saved config. Example: `- Agent 1: Agent 1, codex, gpt-x, high, requested effort xhigh`.

```text
ai-skills pair finalize --run-dir <run-dir> --final-file /path/to/final-solution-draft.md
```

### 7. Present the result

Give the user `final-solution.md` and the run directory path so the whole discussion stays inspectable.

## Resuming

`status --run-dir <dir>` prints the phase and every relevant path without changing state, and
never blocks on the lock. Resume from whatever it reports:

| `STATUS` | Next step |
|----------|-----------|
| `initialized` / `awaiting-agents` | `run-round` |
| `awaiting-coordinator` | Read the round, write the decision, `record-decision` |
| `awaiting-final` | Write the dossier, `finalize` |
| `complete` | Present `final-solution.md` |
| `error` with `COMPLETION_REASON=blocked` | Report the blocker; the run cannot be finalized |

`LIVE_AGENTS` lists any colleague still working from an earlier command. While it is non-empty,
`run-round` returns `4` instead of launching anything.

A run is bound to the repository it was started in, recorded as `WORKSPACE_ROOT`. Every round is
launched there no matter which directory you resume from, so the discussion cannot drift onto a
different repository. A run created before this binding existed reports an empty `WORKSPACE_ROOT`
and must be bound once, explicitly:

```text
ai-skills pair run-round --run-dir <run-dir> --bind-workspace /path/to/repository
```

## Scope

Native Python orchestration runs on macOS, Linux and Windows. Provider CLIs may call remote models. Every colleague uses a registered read-only profile and a durable owned attempt; missing/unknown identity prevents a duplicate launch. Existing snapshot hashes, retry accounting, workspace binding and semantic acceptance rules remain authoritative across resume and updates.
