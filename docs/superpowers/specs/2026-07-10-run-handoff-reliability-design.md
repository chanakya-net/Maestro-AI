# Run Handoff Reliability Design

## Purpose

Make `run-with-it` handoffs deterministic and safe when shared branches move,
workers are quiet, artifacts contain abbreviated commits, reviews retry, and
parallel issues touch overlapping files. The implementation must preserve work
without allowing unverified changes to advance as successful.

## Chosen approach

Use the full correctness design from the diagnosis handoff. A smaller patch
that merely accepts short SHAs, writes a missing sentinel, and uses the remote
branch would retain two unsafe behaviors: unverified salvaged work could be
treated as successful, and overlapping issues would still be admitted
concurrently. The selected design adds explicit invariants at each boundary.

## Invariants

- New issue worktrees start at the fetched remote shared-branch tip when it is
  available; offline operation records and uses an explicit local fallback.
- Dirty or committed recovery work is never reset to refresh its base.
- Normal implementation and modification success requires
  `verification.passed=true`.
- A uniquely resolvable abbreviated commit is canonicalized to the full
  issue-worktree `HEAD`; unknown, ambiguous, baseline, and foreign commits are
  rejected precisely.
- Recovered work without machine-readable passing verification becomes a
  typed `artifact-recovery-required` handoff, never normal success.
- Wrapper-owned heartbeat output distinguishes a live quiet process from a
  process that has lost liveness. A separate hard limit bounds truly stuck
  workers.
- `parallel_safe=false` issues run exclusively. Safe issues run together only
  when normalized ownership scopes do not overlap.
- Reviewer retries exclude the implementation/modification model and every
  failed reviewer model cumulatively.
- A missing optional availability cache is empty state; an existing malformed
  cache remains an error.
- Bash changes remain compatible with macOS Bash 3.2, PowerShell behavior stays
  equivalent, and old state files load with conservative defaults.

## Components and flow

### Artifact boundary

`assets/run-with-it-artifacts.py` canonicalizes Git object identities, validates
passing verification, writes artifacts atomically, and creates an explicit
recovery result for preserved but unverified work. The dispatcher owns terminal
state and sentinel decisions; helpers never manufacture normal success from
unverified repository state.

### Process-liveness boundary

`assets/run-agent.sh` and `assets/run-agent.ps1` emit periodic wrapper heartbeat
events while the external CLI process is alive. Dispatchers track wrapper
heartbeat separately from model stdout. Quiet output is observable but is not
alone a termination reason. The configured hard limit remains the final bound.

### Git-base boundary

The sub-coordinator fetches the named shared branch, chooses
`origin/$RUN_FEATURE_BRANCH` when resolvable, records `issue_base_sha` and the
selection source, and validates the new worktree `HEAD`. Resume refresh is
limited to clean worktrees with no implementation progress.

### Scheduling boundary

State ingestion preserves `parallel_safe` and normalized `ownership_scope`.
Ready selection receives the active issue set and admits candidates only when
their concurrency metadata is compatible. Missing metadata defaults to
exclusive execution for backward-compatible safety.

### Routing and artifact ergonomics

The router accepts repeated model exclusions and unions them deterministically.
Worker prompts pass the implementation model plus all failed review models.
Prompts direct workers to the repository's atomic artifact writer instead of
ad hoc generated quoting scripts. Verification commands are preflighted for
applicability; an inapplicable greenfield baseline is recorded explicitly
instead of being misclassified as a product failure.

## Error handling

- Git canonicalization failures return stable, specific artifact reasons.
- Missing optional files use empty defaults; present invalid files fail fast.
- Heartbeat or hard-limit termination preserves Git progress and routes it to
  artifact recovery.
- Scheduler deferrals are durable structured events, not silent omissions.
- Existing recovery and merge-recovery ownership remains unchanged.

## Testing strategy

Each behavior is introduced test-first in the existing shell contract harness.
Focused tests cover artifact validation, router exclusions/cache handling,
state admission, worktree base selection, Bash and PowerShell liveness parity,
and end-to-end pool scheduling. The final gate runs every `tests/*.test.sh`
suite and checks the staged diff excludes `debug_human_report.md` and
`debug_llm_context.md`.

## Scope boundaries

Do not alter GitHub close/comment ownership, merge-recovery ownership, isolated
worktree architecture, role artifact locations, sticky reviewer convergence,
or target-repository-specific build rules. Do not introduce third-party runtime
dependencies or Bash 4-only syntax.
