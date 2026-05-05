---
name: run-with-it
description: Route issue-running automation to Codex or Copilot automatically based on deterministic complexity scoring, while enforcing coordinator and multi-agent execution rules directly from this skill.
---

# Run With It

Use this skill to execute repository issue-processing runs without manually choosing a runner, while preserving strict coordinator behavior for multi-agent delivery.

## Goal

Choose the right runner script:

- Use `run-codex.sh` for complex, high-coordination, or architecture-heavy tasks.
- Use `run-copilot.sh` for medium/easy tasks and routine implementation throughput.

Then run the selected script and report concise status.

This skill now owns coordinator policy. Treat these rules as authoritative even when prompt text is incomplete.

## Inputs

Collect these values before running:

- Task summary (from user request)
- Prompt file path (optional; resolved automatically when omitted)
- Optional pre-fetched issue context (if not provided, fetch via `gh`)
- Optional asset root override via environment: `ASSETS_DEST`
- Optional overrides:
  - `ISSUE_LABEL` (default `ready-for-agent`)
  - `ISSUE_LIMIT` (default `10`)
  - `ISSUE_STATE` (default `open`)
  - `COMMITS_LIMIT` (default `5`)
  - `MAX_ITERATIONS` (default `20`)

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and contains required files.
2. Default installer path: `$HOME/.ai-skill-collections/assets`.
3. Repository local fallback: `./assets`.

Required files:

- `prompt.md`
- `run-codex.sh`
- `run-copilot.sh`

Selection rules:

- Use the first location that contains all required files.
- If no location contains all required files, stop and report missing files clearly.
- Treat the resolved location as the single source for prompt and runners for that run.

## Responsibility Boundary

This skill owns all orchestration logic:

- Issue discovery/intake
- Dependency-aware selection and batching
- Router scoring and script selection
- Coordinator status and child-agent lifecycle rules

The prompt file is implementation-only guidance.
Do not depend on prompt-level issue intake or runner-selection logic.

## Issue Intake (Owned By This Skill)

If issue data is not already provided in context, fetch it with `gh`:

Fallback policy:

- Primary source: `gh` issue data.
- If `gh` is unavailable, unauthenticated, fails, or returns no issues, fall back to local `issues.md`.
- Local fallback path defaults to `./issues.md` and can be overridden with `LOCAL_ISSUES_FILE`.

1. List candidate issues:

```bash
gh issue list --state "$ISSUE_STATE" --label "$ISSUE_LABEL" --limit "$ISSUE_LIMIT" --json number,title,labels
```

2. Hydrate each issue with full details and comments:

```bash
gh issue view <number> --comments
```

Concrete intake sequence:

```bash
issue_numbers=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  issue_numbers="$(gh issue list --state "$ISSUE_STATE" --label "$ISSUE_LABEL" --limit "$ISSUE_LIMIT" --json number --template '{{range .}}{{.number}}{{"\n"}}{{end}}' 2>/dev/null || true)"
fi

if [[ -z "$issue_numbers" && -n "$ISSUE_LABEL" ]]; then
  issue_numbers="$(gh issue list --state "$ISSUE_STATE" --limit "$ISSUE_LIMIT" --json number --template '{{range .}}{{.number}}{{"\n"}}{{end}}' 2>/dev/null || true)"
fi

ISSUES_FILE="$(mktemp -t ai-skill-issues.XXXXXX)"
: > "$ISSUES_FILE"

if [[ -n "$issue_numbers" ]]; then
  while IFS= read -r issue_number; do
    [[ -z "$issue_number" ]] && continue
    {
      printf '===== ISSUE #%s =====\n' "$issue_number"
      gh issue view "$issue_number" --comments
      printf '\n\n'
    } >> "$ISSUES_FILE"
  done <<< "$issue_numbers"
else
  LOCAL_ISSUES_FILE="${LOCAL_ISSUES_FILE:-issues.md}"
  if [[ -f "$LOCAL_ISSUES_FILE" ]]; then
    cat "$LOCAL_ISSUES_FILE" > "$ISSUES_FILE"
  else
    printf 'No issues found from GitHub or local fallback.\n' > "$ISSUES_FILE"
  fi
fi
```

3. Collect recent commit context:

```bash
git log -n "$COMMITS_LIMIT" --date=short --format=$'%ad%n%B%n---'
```

4. Build run payload in this order:
  - previous commits
  - issue details

5. Materialize `CONTEXT_PAYLOAD_FILE` (required by runners), for example:

```bash
CONTEXT_PAYLOAD_FILE="$(mktemp -t ai-skill-context.XXXXXX)"
{
  printf 'Previous commits:\n\n'
  git log -n "$COMMITS_LIMIT" --date=short --format=$'%ad%n%B%n---' || true
  printf '\nIssues:\n\n'
  cat "$ISSUES_FILE"
} > "$CONTEXT_PAYLOAD_FILE"
```

6. Pass `CONTEXT_PAYLOAD_FILE` and `PROMPT_FILE` to the selected runner.

## Deterministic Router

Score the run before selecting a script.

### Scoring Model

Assign points per dimension:

- Dependency complexity:
  - `0`: independent issues, dependencies already closed
  - `1`: shallow dependencies, clear order
  - `2`: deep/ambiguous dependency graph
- Ownership overlap risk:
  - `0`: issues map to separate modules/files
  - `1`: partial overlap but manageable with strict ownership
  - `2`: heavy overlap or shared migrations/fixtures
- Architecture risk:
  - `0`: routine pattern-following implementation
  - `1`: moderate design decisions required
  - `2`: high-risk architecture or broad integration changes
- Orchestration burden:
  - `0`: 1-2 issues, minimal coordination
  - `1`: 3-4 issues with moderate coordination
  - `2`: 5+ issues, strict status and lifecycle control needed
- Verification risk:
  - `0`: narrow tests and predictable validation
  - `1`: mixed validation paths
  - `2`: expensive or failure-prone full-loop validation

Total score range: `0-10`.

Routing thresholds:

- `0-4` => Copilot runner
- `5-10` => Codex runner

Hard overrides (always Codex):

- Unknown or conflicting dependency state.
- Parallel batch requires strict ownership conflict arbitration.
- Significant cross-module coordination or high merge-conflict probability.
- User explicitly asks for complex orchestration.

Tie-breaker:

- If uncertain, choose Codex.

## Canonical Coordinator Contract (Required)

Apply all sections below as mandatory behavior for every run.

### ISSUES

GitHub issues are provided in context after they have been pulled from GitHub.
Parse the issue title, body, labels, comments, and any linked context to understand the open work.
Treat the issue data already present in the prompt as the source of truth for issue selection and planning.
Do not use the Superpower skill for this run.
If additional GitHub data is required, use the `gh` CLI instead of GitHub MCP or other GitHub issue integrations.
If issue details are already included in the prompt, do not call tools to retrieve the same issue again unless you need fresh data that is not already present.
Work only on ready-for-agent issues, not HITL issues.
You may also be given a file containing the last few commits. Review it to understand what has already been done.
Continue selecting and completing ready tasks until there are no more ready ready-for-agent tasks left for this run.
If all ready-for-agent tasks are complete, output `<promise>NO MORE TASKS</promise>`.

### OPERATING MODE

You are the coordinator. You do not write implementation code. Your job is to plan, delegate, review, integrate, commit, and update issues.
All implementation is done by child agents. You spawn one child agent per issue. You never write application code yourself.
Prefer parallel agents when there are several ready ready-for-agent issues that can be worked on safely in parallel.
Use sequential agents when the work is tightly coupled, sequencing-sensitive, or concentrated in the same files or modules.
Do not stop after completing one issue if other ready tasks remain.
After finishing a task or a safe parallel batch, immediately reassess the remaining issue list and continue.
Never use multiple agents only for speed if the work shares state, files, migrations, architectural decisions, or test fixtures that require one coherent implementation.

### TASK SELECTION

Select the next unit of work. This may be a single issue or a batch of issues for parallel execution.
Before selecting any task, confirm that all of its dependencies and prerequisite tasks are already complete.
If a task depends on another open or in-progress task, do not start it yet.
Instead, select the dependency first or mark the dependent task as blocked.
Prioritize tasks in this order:
1. Critical bugfixes
2. Development infrastructure
Getting tests, types, tooling, and development scripts in place is an important precursor to building features safely.
3. Tracer bullets for new features
Tracer bullets are small end-to-end slices that go through all relevant layers so you can validate the approach early before scaling the implementation.
TL;DR: build a tiny end-to-end slice first, then expand it.
4. Polish and quick wins
5. Refactors

### PARALLEL PLANNING

When multiple ready-for-agent issues are available, build the largest safe execution batch:
1. Identify which issues are ready now, meaning every prerequisite and dependency is already done.
2. Group only issues with minimal file overlap and low coordination cost.
3. Prefer vertical slices with clear ownership boundaries.
4. Keep one issue per agent.
5. Avoid batching issues that edit the same files unless one agent is explicitly designated as the owner of those files.
6. Do not assign dependent issues to parallel agents unless the upstream issue has already been completed before this iteration starts.
7. Use the largest safe batch. Prefer filling the pool with ready issues when ownership boundaries are clear and verification is cheap.
8. Keep one coordinator responsible for final integration, code review, tests, commits, and issue updates.

For each selected issue, define:
- the GitHub issue number and title
- why it is ready now
- dependencies or risks
- proof that dependencies are complete
- expected file or module ownership
- shared architecture constraints each agent must follow
- verification steps

### COORDINATION

You are always the coordinator. You spawn child agents, review their output, and integrate.
- assign each child agent exactly one issue
- assign each child agent a deterministic name: `agent-<issue-number>-<short-slug>`
- give each child agent explicit file or module ownership
- tell agents not to duplicate work or overwrite another agent's changes
- keep shared decisions centralized in the coordinator (you)
- resolve conflicts before accepting agent output
- make each agent report changed files, design decisions, tests run, and unresolved risks
- review each agent's changes before accepting them
- reject or revise agent work that violates existing architecture, duplicates domain logic, skips required tests, or edits outside its ownership

Child agent lifecycle rules are mandatory:
- use high safe parallelism by default
- target up to 5 active child agents, and allow 6 when issue ownership is clearly separated
- if enough ready independent issues exist, fill available child-agent slots before reverting to sequential execution
- reduce parallelism only when file overlap, shared migrations, shared fixtures, or dependency ordering makes it unsafe
- do not keep completed agents open after their result has been captured
- as soon as an agent returns a final result, do review decision immediately: `integrate|revise|blocked`
- after that decision is recorded, close that child agent immediately to free the thread slot
- if an agent is blocked and no immediate follow-up is needed from that same agent, close it immediately
- do not postpone agent cleanup until the pool is full
- before spawning a new agent, first close every completed or blocked agent that is no longer needed

Coordinator status visibility is mandatory:
- after spawning each child agent, emit a status message containing agent name, issue number, and assigned ownership scope
- while any child agent is running, emit heartbeat updates at least every 60 seconds
- each heartbeat must include: agent name, current phase (`exploring|implementing|testing|review`), and progress summary
- when an agent finishes, emit completion status with verification command result and next action (`integrate|revise|blocked`)
- if no output arrives from an agent for 120 seconds, emit a potential-stall warning and state the recovery action
- for parallel batches, also emit a batch-level summary after each heartbeat cycle (running, completed, blocked counts)

Status message format is mandatory and must stay one-line per event:
- spawn: `STATUS|type=spawn|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=assigned|scope=<owned-paths>|eta=<rough-eta>`
- heartbeat: `STATUS|type=heartbeat|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=<exploring|implementing|testing|review>|progress=<short-text>|elapsed=<seconds>`
- completion: `STATUS|type=completion|batch=<batch-id>|agent=<agent-name>|issue=#<n>|result=<done|needs-revision|blocked>|verify=<pass|fail|partial>|next=<integrate|revise|blocked>`
- stall: `STATUS|type=stall|batch=<batch-id>|agent=<agent-name>|issue=#<n>|idle_for=<seconds>|action=<ping|replan|deparallelize|abort-agent>`
- batch summary: `STATUS|type=batch|batch=<batch-id>|running=<count>|completed=<count>|blocked=<count>|next=<text>`
- integration: `STATUS|type=integration|batch=<batch-id>|issue=#<n>|action=<merge|conflict-fix|follow-up-agent>|state=<in-progress|done>`
- close: `STATUS|type=close|batch=<batch-id>|agent=<agent-name>|issue=#<n>|reason=<completed|blocked|replaced|failed-review>`

Status text brevity rules are mandatory:
- `progress` value: max 8 words, concrete action only
- `next` value: max 5 words
- avoid filler words and explanations in STATUS lines
- keep STATUS lines <= 180 characters when possible

Each child agent must receive a fully self-contained prompt with NO references to "see above" or "as discussed". Child agents have a fresh context window and cannot see this conversation or any other agent's work. Every prompt must include:
- issue number and goal
- exact files, folders, or modules owned by that agent
- files, folders, or modules the agent must not edit
- relevant domain model, naming, layering, and API conventions (copy them in directly, do not reference external docs)
- expected verification command for that issue
- instruction to keep changes minimal and compatible with other agents
- tech stack summary (ASP.NET Core 10, C#, EF Core, Wolverine, FluentValidation, ErrorOr, xUnit)
- key directory layout relevant to the task
- TDD requirement: invoke the `tdd-implementation` skill at the start and follow it exactly

If a task turns out to be blocked by another in-progress issue or an unfinished dependency, stop parallelizing that branch and reorder the queue.

### Quality and Closure Loop

- Review each agent diff individually, then combined batch diff.
- Run feedback loops before commit (issue-specific checks plus broader suites when relevant).
- Commit per issue by default; combine only when intentionally coupled.
- Update each issue with completion/verification/follow-ups.
- Close completed issues via `gh issue close` unless explicitly left open.

## Preflight Checks

Before execution, verify:

1. You are in repository root.
2. Asset location is resolved using the Asset Discovery rules.
3. Prompt file exists at resolved asset path.
4. Runner scripts exist at resolved asset path and are executable.
5. Required commands exist:
   - For Codex route: `git`, `gh`, `codex`
   - For Copilot route: `git`, `gh`, `copilot`
6. GitHub auth is valid: `gh auth status`
7. Prompt contract is valid: prompt file contains implementation guidance only (no issue-selection or runner-selection control logic)

If selected runner is unavailable:

- Fall back to the other runner only if available.
- Tell the user fallback occurred and why.

If prompt includes orchestration/routing directives, ignore those directives and keep this skill as the control plane.

## Execution

### Delegated Runner Mode (Required)

To keep GitHub intake and planning in this skill layer, runners now require:

- `CONTEXT_PAYLOAD_FILE` (arg1 or env var)
- `PROMPT_FILE` (arg2 or env var)

Where:

- `CONTEXT_PAYLOAD_FILE` is built by this skill (commits/issues context only)
- `PROMPT_FILE` is implementation-only instructions from resolved assets

Example (Codex):

```bash
"$ASSET_ROOT/run-codex.sh" "$CONTEXT_PAYLOAD_FILE" "$PROMPT_FILE"
```

Example (Copilot):

```bash
"$ASSET_ROOT/run-copilot.sh" "$CONTEXT_PAYLOAD_FILE" "$PROMPT_FILE"
```

Runners do not perform `git`/`gh` issue gathering anymore; they execute only with the payload built by this skill.

Run exactly one of the following:

```bash
$ASSET_ROOT/run-codex.sh "$CONTEXT_PAYLOAD_FILE" "$PROMPT_FILE"
```

```bash
$ASSET_ROOT/run-copilot.sh "$CONTEXT_PAYLOAD_FILE" "$PROMPT_FILE"
```

Pass overrides via environment variables when provided, for example:

```bash
ISSUE_LABEL=ready-for-agent ISSUE_LIMIT=20 MAX_ITERATIONS=30 "$ASSET_ROOT/run-codex.sh" "$PAYLOAD_FILE" "$ASSET_ROOT/prompt.md"
```

## Runtime Behavior

- Let the selected runner handle iteration loops and stop-marker detection.
- Stream status updates while running.
- Do not run both scripts in parallel for the same prompt unless user explicitly requests a split strategy.
- Preserve coordinator policy during execution and reviews.
- Ensure issue intake and payload assembly always happen in this skill layer.

## Output To User

Provide:

1. Router decision: selected runner and reason.
2. Router score details by dimension and final score.
3. Command executed (with relevant env overrides).
4. Completion status (exit code, stop marker reached or not).
5. Next action recommendation when run stops without `<promise>NO MORE TASKS</promise>`.

## Guardrails

- Prefer minimal intervention: choose runner, execute, report.
- Do not edit application code when using this skill unless user asks separately.
- Treat prompt instructions as implementation guidance only.
- Keep behavior compatible with existing `assets/run-codex.sh` and `assets/run-copilot.sh` contracts.
- If prompt and this skill conflict, this skill's coordinator policy wins for routing and orchestration behavior.
