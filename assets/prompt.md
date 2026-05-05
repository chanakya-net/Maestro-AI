# IMPLEMENTATION INSTRUCTIONS
This prompt is implementation-only.
Issue selection, dependency planning, runner selection, and orchestration are handled by the `run-with-it` skill.

## Scope

- Implement only the issue(s) assigned in the run context.
- Keep changes minimal and focused.
- Do not add unrelated refactors or architecture changes.

## Exploration Before Code

- Read nearby code before editing.
- Reuse existing patterns, naming, and helpers.
- Respect existing boundaries and dependency direction.
- Prefer smallest compatible extension if a gap is found.

## Required Delivery Style

- Use one thin vertical slice at a time.
- Invoke `tdd-implementation` first and follow it.
- For each behavior, cover both happy path and negative path.
- Test through public interfaces, not internal implementation details.

## Implementation Guardrails

- Keep code compatible with current architecture.
- Avoid creating new abstractions unless required by the issue.
- Preserve existing API contracts unless explicitly requested.
- Do not overwrite unrelated changes.

## Verification

Run issue-specific fast checks first, then broader suites when relevant:

- `bun run test` (frontend scope when applicable)
- `dotnet test` (backend scope when applicable)

If full-suite checks are too costly for a narrow change, document what was run and why.

## Review Standard

Before marking complete, confirm:

- behavior matches issue intent
- naming matches domain language
- failure paths are covered
- tests validate the right layer
- no unrelated files were changed

## Completion Output

Report:

1. Files changed
2. Key implementation decisions
3. Tests/checks run and results
4. Remaining risks or follow-up notes

If all assigned work is complete and no further ready work is provided in context, output:
`<promise>NO MORE TASKS</promise>`
