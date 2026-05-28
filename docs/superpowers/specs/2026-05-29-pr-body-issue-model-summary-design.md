# PR Body Issue And Model Summary Design

## Goal

When `run-with-it` creates its final pull request, the PR body should clearly show:

1. The GitHub issues that were completed and closed during the run, as plain issue links such as `#123`.
2. Which agent/model pair was used for each task role, grouped by issue and role.

The PR body must not use GitHub auto-closing keywords such as `Closes`, `Fixes`, or `Resolves`.

## Current State

Final PR creation is currently instruction-driven in `skills/run-with-it/SKILL.md` and `assets/main-orchestrator-rules.md`. The pool runner and `run-with-it-state.py` already persist compact per-issue summaries in `.run-with-it/main-state.json`, but those summaries do not yet include task-level model routing details. Sub-Coordinators route workers through `run-with-it-router.py`, and route/status lines already contain role, agent, and model data.

## Proposed Approach

Add a helper-backed final PR body renderer, owned by the run-with-it control plane.

The helper reads `.run-with-it/main-state.json` plus each issue's compact report JSON when available. It renders a deterministic Markdown PR body with these sections:

- `## Summary`: compact run totals and outcome counts.
- `## Closed Issues`: one bullet per completed issue, formatted as `- #<issue>`.
- `## Models Used`: a table with issue, task role, agent, model, and selection reason when available.
- `## Verification`: compact verification outcomes from completed issue reports.

The Main Orchestrator remains responsible for running `gh pr create`; it must use the helper output as the PR body file. This keeps PR creation behavior under orchestrator control while making the body content deterministic and testable.

## Data Flow

Sub-Coordinators should include task-level routing metadata in compact reports under a small field such as `model_usage`.

Each entry should include:

- `role`: `complexity`, `impl`, `review`, `modify`, or `merge-recovery`.
- `cycle`: integer when applicable.
- `agent`: selected agent slug.
- `model`: selected model id.
- `selection_reason`: optional router reason.

`run-with-it-state.py finalize-issue` should preserve this compact `model_usage` field in `completed_summaries`, so the final PR renderer can survive context compression without reading raw logs.

## Error Handling

If model usage is missing from older reports, the renderer should still generate the PR body and show `unknown` for missing agent/model values. Missing or invalid report files should not block PR body generation.

If no completed issues exist, the `Closed Issues` section should say `None`.

## Tests

Add focused tests for:

- State finalization preserving compact `model_usage`.
- PR body rendering linked issues as `#123` without closing keywords.
- PR body rendering task-level agent/model rows.
- Missing report or missing model usage fallback to `unknown`.

Update existing run-with-it contract tests to require the final PR body renderer and final PR instructions.
