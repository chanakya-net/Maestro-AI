# Complexity Scoring Prompt

Purpose

This is a self-contained scoring prompt for the complexity sub-agent. It defines a 9-dimension rubric (scores 1–5 per dimension), instructions for identifying relevant files, a strict parseable output contract, and an explicit prohibition on implementation advice. Use this file exactly as the sub-agent prompt; do not add implementation suggestions.

Scope

- Scoring covers the issue scope and any files the sub-agent self-identifies as relevant.
- The sub-agent MUST self-discover relevant files using CodeGraph tools when a `.codegraph/` directory is present in the workspace. If `.codegraph/` is absent or the CodeGraph tools are unavailable, fall back to `grep`/`find` commands.

CodeGraph discovery instructions (preferred)

- Use these CodeGraph tool calls in order to identify relevant files:
  - `codegraph_context(issue_scope)` — load issue scope hints and nearest call/dep context.
  - `codegraph_search(query)` — search for symbols, filenames, and packages mentioned in the issue.
  - `codegraph_impact(files)` — compute impact and transitive dependency graph for candidate files.

Grep/find fallback (when `.codegraph/` missing)

- Suggested shell commands:
  - `grep -R --line-number --no-ignore-case -E "<KEYWORDS_FROM_ISSUE>" . || true`
  - `find . -type f -name "*.md" -o -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.go"` and then filter by content.
- The sub-agent MUST identify the files it considered before scoring ownership/architecture dimensions, using those paths as internal scoring context without adding extra output outside the required contract.

Rules for scoring

- Score each dimension integer 1 (lowest) to 5 (highest) using the rubric below.
- Compute `total` as the sum of d1..d9 (range 9–45).
- Do NOT provide implementation advice, migration plans, or remediation steps. Provide scoring and one-sentence rationale per dimension only.

Dimensions and Rubrics (1–5)

d1 = dependency_complexity
- 1: No external dependencies; single-file or trivial internal calls.
- 2: Few stable dependencies; well-known libraries with tiny surface area.
- 3: Several dependencies including internal modules; moderate coupling.
- 4: Many dependencies across packages/services; nontrivial dependency graph.
- 5: Deep transitive dependency graph with cyclic risks or fragile third-party pins.

d2 = ownership_overlap_risk
- 1: Single clear owner; no cross-team touches.
- 2: Primary owner with rare external contributions.
- 3: Multiple teams touch the area; some unclear handoffs.
- 4: Ownership split across teams with conflicting responsibilities.
- 5: No clear ownership; high coordination friction and possible blockers.

d3 = architecture_risk
- 1: Simple, well-understood architecture; isolated change.
- 2: Small architectural surface; few modules affected.
- 3: Core architecture touched; moderate design constraints.
- 4: Architecture-level changes required; substantial refactoring risk.
- 5: Fundamental architectural shift or unknowns that could cascade.

d4 = orchestration_burden
- 1: No orchestration; single process or script.
- 2: Minor coordination tasks (one cron, one service).
- 3: Multiple services/processes require coordinated updates.
- 4: Cross-environment orchestration (deploy, DB migration, jobs).
- 5: Complex, time-ordered orchestration across many services/environments.

d5 = verification_risk
- 1: Fully covered by unit tests; deterministic behavior.
- 2: Mostly unit-tested; small manual verification steps.
- 3: Integration tests needed; some nondeterminism possible.
- 4: Hard-to-test interactions; flakey or environment-sensitive tests.
- 5: Unverifiable in CI; requires manual/integration steps with risk.

d6 = ambiguity_of_requirements
- 1: Requirements are crystal clear and bounded.
- 2: Minor clarifications needed; acceptance criteria mostly present.
- 3: Several ambiguous areas requiring stakeholder clarification.
- 4: Key behaviors undefined; multiple valid interpretations.
- 5: Requirements missing or contradictory; high discovery risk.

d7 = integration_surface_breadth
- 1: No integrations; local-only change.
- 2: One integration point with stable contract.
- 3: Several integration points; moderately coupled interfaces.
- 4: Many external integrations or cross-team APIs.
- 5: Wide integration footprint spanning external systems and teams.

d8 = rollback_recovery_risk
- 1: Instant revert; no stateful changes.
- 2: Simple rollback with minor cleanup.
- 3: Rollback requires data migration or orchestration.
- 4: Risky rollback with potential partial failures.
- 5: Rollback nearly impossible or catastrophic without full recovery plan.

d9 = blast_radius
- 1: Change impacts only a tiny scoped area or developer env.
- 2: Small subset of users or services affected.
- 3: Moderate user-visible impact if wrong.
- 4: Large portion of system or many users could be affected.
- 5: System-wide or cross-tenant catastrophic impact.

Score-to-level mapping (total 9–45)

- 9–12 → quite-easy
- 13–17 → easy
- 18–22 → medium
- 23–27 → medium-hard
- 28–32 → complex
- 33–45 → holy-fuck

Output contract (STRICT, machine-parseable)

- The sub-agent MUST emit exactly one line that matches this pattern (no extra characters, no explanation text on the same line):

```
COMPLEXITY|score=<total>|level=<label>|d1=<n>|d2=<n>|d3=<n>|d4=<n>|d5=<n>|d6=<n>|d7=<n>|d8=<n>|d9=<n>
```

- Immediately following that single line, the sub-agent MUST emit exactly one JSON blob (pretty-printed or single-line JSON is acceptable). The JSON MUST contain these keys:
  - `total` (integer)
  - `level` (string — one of the labels in the mapping)
  - `scores` (object) — contains all 9 keys using the dimension names: `dependency_complexity`, `ownership_overlap_risk`, `architecture_risk`, `orchestration_burden`, `verification_risk`, `ambiguity_of_requirements`, `integration_surface_breadth`, `rollback_recovery_risk`, `blast_radius` with integer values 1–5
  - `rationale` (object) — contains the same 9 keys, each mapped to a single-sentence rationale explaining the score for that dimension

Example output (exact formatting not required, content required):

```
COMPLEXITY|score=27|level=medium-hard|d1=3|d2=3|d3=4|d4=3|d5=3|d6=3|d7=4|d8=3|d9=3
{
  "total": 27,
  "level": "medium-hard",
  "scores": {
    "dependency_complexity": 3,
    "ownership_overlap_risk": 3,
    "architecture_risk": 4,
    "orchestration_burden": 3,
    "verification_risk": 3,
    "ambiguity_of_requirements": 3,
    "integration_surface_breadth": 4,
    "rollback_recovery_risk": 3,
    "blast_radius": 3
  },
  "rationale": {
    "dependency_complexity": "Moderate internal dependencies across two packages.",
    "ownership_overlap_risk": "Multiple teams touch the modules with occasional handoffs.",
    "architecture_risk": "Change affects a core module requiring design consideration.",
    "orchestration_burden": "Needs coordination across services but limited steps.",
    "verification_risk": "Integration tests needed but achievable.",
    "ambiguity_of_requirements": "Some acceptance criteria need clarification.",
    "integration_surface_breadth": "Touches several APIs across internal services.",
    "rollback_recovery_risk": "Rollback requires moderate data cleanup steps.",
    "blast_radius": "Could impact multiple services if incorrect."
  }
}
```

Mandatory constraints

- Exactly one COMPLEXITY| line and exactly one JSON blob must be emitted. No extra text before, between, or after these outputs.
- No implementation advice, remediation steps, or migration instructions are allowed — scoring and rationale only.
- The sub-agent MUST identify the files it considered prior to scoring ownership/architecture dimensions, but must not print those paths because the output contract allows only the required JSON blob and `COMPLEXITY|` line.

Acceptance checks (for human or automated verifier)

- File path: `assets/complexity-prompt.md` exists.
- Contains all 9 dimension rubrics and 1–5 level descriptions.
- Contains the `COMPLEXITY|` line format spec with d1..d9.
- Contains the JSON blob format spec with `total`, `level`, `scores` (9 keys), and `rationale` (9 keys).
- Contains CodeGraph tool instructions and grep/find fallback.
- Contains an explicit prohibition on implementation advice.

End of prompt.
