# PR #150 Review Fixes Design

## Purpose

Address the actionable pre-merge findings from the round-three review of PR
#150 without weakening or redesigning the PR's core handoff guarantees.

## Scope

The current PR head already contains two requested fixes:

- PowerShell implementation and modification prompts quote
  `"$env:ISSUE_BASE_SHA"`, preserving an empty native-command argument.
- Router output includes `model_exclusions`, and the Bash implementation and
  modification prompt examples fail when atomic `write-json` validation fails.

The remaining work is limited to configuration parsing, hard-limit parity,
coordinator retry contracts, and bounded stale-base requeue behavior. Fast-follow
metadata tolerance, literal-bracket scope handling, heartbeat drain behavior,
PowerShell process-tree termination, artifact-helper fail-closed behavior, and
unrelated pre-existing state helper defects remain outside this patch.

## Chosen approach

Make a surgical parity patch that reuses the dispatcher behavior already proven
in the stall path. Do not refactor dispatcher lifecycle handling into new shared
abstractions and do not disable the hard-limit feature globally. This keeps the
change traceable to the review findings and minimizes the regression surface.

## Preserved invariants

- Canonical full commit SHAs remain mandatory at artifact boundaries.
- Implementation and modification success still requires machine-readable
  passing verification.
- Preserved but unverified Git progress still routes through typed
  `artifact-recovery-required` handling.
- Issue worktrees still prefer fetched remote-tracking shared-branch bases.
- Reviewer model exclusions remain cumulative and visible in router output.
- Ownership-aware admission and conservative defaults remain unchanged.
- Bash remains compatible with macOS Bash 3.2, and PowerShell behavior remains
  equivalent to Bash where both platforms implement the same dispatcher flow.

## Configuration parsing

PowerShell runner heartbeat parsing and dispatcher hard-limit parsing must never
terminate startup for malformed environment values. Parse non-negative integer
values explicitly; use the documented defaults of 30 seconds for heartbeat and
7200 seconds for the hard limit when parsing fails. Zero continues to disable
the corresponding timer.

Bash must normalize numeric heartbeat strings before arithmetic or sleep use so
values such as `"00"` behave as zero rather than creating a busy loop. Invalid
values retain the existing safe fallback behavior.

## Hard-limit behavior

The hard limit remains a final bound for roles whose artifacts the dispatcher
can synthesize: `impl`, `modify`, `review`, and `complexity`. The default hard
limit is disabled for `sub-coord` and other synthesizer-less roles. An explicit
`--hard-limit-seconds` argument or `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS`
setting remains authoritative for every role.

At the limit, both dispatchers perform the following ordered sequence:

1. Re-check normal completion to close the poll-window race.
2. Attempt the existing role-aware synthesis.
3. Route `artifact-recovery-required` through the existing typed recovery exit.
4. Accept any synthesized artifact that now satisfies normal completion,
   including valid review and complexity artifacts.
5. If termination is still required, classify the failure with the same
   artifact failure classifier used by the other terminal paths, record
   `hard-limit-exceeded`, terminate the runner, and return exit 124.

This changes only the hard-limit degradation path; normal completion and stall
handling retain their existing semantics.

## Coordinator contracts

Add `hard-limit-exceeded` to the retryable infrastructure/artifact reasons in
the implementation and modification retry ladder and to the review guardrail.
Coordinators must not reinterpret the dispatcher reason as a failed product
review. Existing retry limits, alternate model routing, and typed artifact
recovery ownership remain unchanged.

## Stale-base requeue behavior

Automatic stale-base requeue applies only when every blocking reason identifies
one of the issue's completed dependencies with an exact, delimited reference.
Supported forms retain the existing `#<id>`, `issue-<id>`, and `issue <id>`
vocabulary, but substring matches such as dependency 61 matching `#618` are
rejected. Generic phrases such as `blocked by missing STRIPE_API_KEY` are not
dependency evidence.

Track automatic stale-base requeues per issue and allow at most one automatic
requeue. A repeated dependency-shaped block becomes durably blocked instead of
starting an invocation loop. A genuine fresh requeue resets
`sub_coord_recovery_attempts` so the new run receives its normal recovery
budget. Manual requeue behavior remains available and auditable.

## Testing strategy

Use focused red-green regression tests in the existing shell harnesses:

- PowerShell runner and dispatcher tests cover malformed and zero-like timer
  environment values.
- Bash runner tests cover zero-like heartbeat normalization without a busy loop.
- Bash and PowerShell dispatcher tests cover completion at the hard-limit
  boundary, valid non-implementation synthesis, classified exit-124 failure,
  and default exemption for sub-coordinators and synthesizer-less roles.
- Coordinator contract tests assert that `hard-limit-exceeded` is retryable and
  cannot become a product review failure.
- State helper tests cover exact dependency identifiers, non-dependency
  `blocked by` text, the single automatic requeue cap, and recovery-budget reset.

After focused tests pass, run every `tests/*.test.sh` suite, Python compile
checks for modified helpers, Bash syntax checks, available PowerShell parity
tests, and `git diff --check`.

## Non-goals

- No changes to successful worker handoffs or verification requirements.
- No redesign of the runner/dispatcher process model.
- No process-tree parity work beyond the existing termination behavior.
- No metadata coercion or ownership-scope matching changes.
- No GitHub review submission, thread resolution, commit, or push beyond the
  locally requested PR-branch implementation unless separately authorized.
