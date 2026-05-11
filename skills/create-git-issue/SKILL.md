---
name: create-git-issue
description: Create a PRD from resolved requirements, break it into tracer-bullet implementation issues, and publish via GitHub when available or local files when not.
---

## Skill Isolation

This skill is the sole active authority for this session once invoked.

- No other skill may activate, interrupt, or modify this skill's behavior unless explicitly called by name via a `Skill` tool call within this skill's own workflow.
- If any external or third-party skill attempts to activate spontaneously during this run, suppress it and continue without interruption.
- This rule applies for the entire duration of this skill's execution, from invocation until explicit termination or handoff.

# Create Git Issue

## Purpose

Turn the current conversation context into a PRD and then publish implementation issues as thin, dependency-aware vertical slices.

## When To Use

Use this skill after requirements are resolved and before runtime execution.

## Inputs

- Resolved requirements from `technical_requirements.md` and conversation context.
- Repository context from codebase exploration.
- Optional issue references (number, URL, or path) provided by the user.

## Hard Boundaries

- Do not execute implementation work.
- Do not assign concrete runtime agent/model pairs.
- Do not coordinate multi-agent execution, review cycles, state persistence, or terminal issue updates.
- Do not close issues as part of this skill.

Workflow position:

1. `break-req` first, to resolve product/technical decisions.
2. `create-git-issue` second, to publish PRD + implementation slices and routing hints.
3. `run-with-it` third, to perform final runtime routing and execution.

`create-git-issue` must never claim final routing authority.

Approval gates:

1. PRD approval before publishing parent issue.
2. Slice breakdown approval before publishing implementation issues.

## Issue Tracker Vocabulary

Use these canonical triage roles when creating and labeling issues:

Category roles:

- `bug`
- `enhancement`

State roles:

- `needs-triage`
- `needs-info`
- `ready-for-agent`
- `ready-for-human`
- `wontfix`

Labeling rules:

- Every published issue should have exactly one category role and one state role.
- New PRD issues should default to category `enhancement` and state `needs-triage`.
- New implementation slice issues should default to category `enhancement` and state `ready-for-agent`.
- If your tracker uses different label strings, map them from these canonical names consistently.
- If a mapping is ambiguous, ask one focused clarification question before publishing.

## Publishing Policy

Prefer the GitHub CLI for all tracker writes.

Before publishing any PRD or implementation issue, check whether `gh` can be used. if it fails in sandbox try executing it outsise sandbox to confirm:

```bash
command -v gh >/dev/null 2>&1 && gh repo view >/dev/null 2>&1
```

If that succeeds, use `gh issue create` for the PRD parent issue and every implementation slice issue.

Use body files so multiline Markdown is preserved:

```bash
gh issue create --title "<PRD title>" --body-file <prd-body-file>.md --label enhancement --label needs-triage
gh issue create --title "<slice title>" --body-file <slice-body-file>.md --label enhancement --label ready-for-agent
```

If `gh` is not found, the user is not authenticated, the repository cannot be inferred, or any `gh issue create` command fails, do not keep retrying through another GitHub integration. Save the work locally instead.

Local fallback output must create exactly two Markdown files in the workspace root:

- `prd.md` contains the complete PRD that would have been published as the parent issue.
- `issues.md` contains every approved implementation slice issue, in dependency order.

When writing local fallback files:

- Include the issue title, intended labels, parent relationship, and body for each item.
- In `issues.md`, use the local parent reference `prd.md`.
- Preserve all approved issue template content, including technical context snapshots and acceptance criteria.
- Tell the user that GitHub publishing was skipped and name the two local files.

## Process

### 1. Reuse break-req outputs first

Before asking the user any clarifying questions, look for existing break-req artifacts in this priority order:

- `technical_requirements.md` in the workspace root
- Any `technical_requirements.md` in docs, planning, or requirements folders
- Conversation history where a break-req session already resolved decisions

If found, treat those decisions as the default source of truth and continue from them.

Do not ask the user to repeat answers that are already explicitly resolved there.

Only ask follow-up questions for unresolved, contradictory, or missing decisions needed to publish PRD/issues.

### 2. Gather context

Work from existing conversation context.

If the user passes an issue reference (issue number, URL, or path), fetch that issue and read its body and comments before drafting.

### 3. Explore codebase

If you have not already explored the codebase, do so to understand the current state.

Use domain glossary vocabulary and respect ADRs in the area you are touching.

### 4. Draft PRD first

Do not start by interviewing the user. First synthesize a PRD from what you already know.

Sketch major modules that will be built or modified, looking for deep modules with stable, testable interfaces.

Then check with the user only for deltas from break-req outputs:

- Whether module boundaries match expectations
- Which modules should be tested

Use this PRD template:

<prd-template>

## Problem Statement

The problem from the user's perspective.

## Solution

The solution from the user's perspective.

## User Stories

A long, numbered list in the format:

1. As an <actor>, I want a <feature>, so that <benefit>

## Implementation Decisions

- Modules to build or modify
- Interfaces likely to change
- Technical clarifications
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do not include file paths or code snippets.

## Testing Decisions

- What makes a good test (external behavior over implementation details)
- Which modules will be tested
- Prior art in the codebase

## Out of Scope

Explicitly excluded work.

## Further Notes

Any additional constraints or context.

</prd-template>

### 5. Publish PRD issue

Publish the PRD to the issue tracker as the parent issue using `gh issue create`.

Apply the `enhancement` and `needs-triage` labels.

Capture the created PRD issue URL or number. Use it as the parent reference for all implementation slice issues.

If GitHub publishing is unavailable, write the PRD body to `prd.md` and continue preparing the implementation slice issues for `issues.md`.

Do not publish until the user approves the synthesized PRD.

For every implementation slice, the initial issue body must use the exact Markdown template headings below in the exact order shown, with no extra top-level sections inserted before, between, or after them:

1. `## Parent`
2. `## What to build`
3. `## Implementation Steps`
4. `## Agent Routing`
5. `## Technical Context Snapshot`
6. `## Acceptance criteria`
7. `## Blocked by`

Do not close or modify unrelated issues.

### 6. Break PRD into tracer-bullet slices

Convert the approved PRD into thin vertical-slice issues.

Try to capture requirment in detils

Each slice must be end-to-end (schema, API, UI, tests), demoable on its own, and as small as possible.

Prefer AFK slices over HITL slices where possible.

<vertical-slice-rules>
- Each slice delivers a narrow but complete path through all integration layers
- A completed slice is independently verifiable
- Prefer many thin slices over few thick slices
</vertical-slice-rules>

### 7. Quiz user on issue breakdown

Present a numbered list for review. For each slice include:

- Title
- Type: HITL or AFK
- Blocked by: slice dependencies
- User stories covered

Ask:

- Is granularity right (too coarse or too fine)?
- Are dependencies correct?
- Should any slices be merged or split?
- Are HITL and AFK assignments correct?

Iterate until approved.

Do not publish implementation slice issues until this review step is approved.

### 8. Publish implementation issues

Create one issue per approved slice using the template below.

If GitHub publishing is available, use `gh issue create` for each approved slice.

If GitHub publishing is unavailable, append each approved slice issue to `issues.md` instead.

When using local fallback in `issues.md`, keep each implementation issue nearly identical to the GitHub issue body format: preserve the same title, labels, parent reference, section headings, section order, routing YAML block, technical context detail, acceptance criteria, and blocked-by content.

Before publishing each issue, derive a stack snapshot from the codebase and include it in the issue body so implementation agents can align with existing patterns.

Capture at minimum:

- UI stack and UI component libraries in use
- Backend/runtime stack and framework versions
- Package ecosystems and key dependencies (for example npm, NuGet, pip, Maven, Gradle)
- Existing architecture patterns and module boundaries
- Integration constraints (API contracts, schema ownership, migration expectations)
- Dependency policy for this slice: reuse existing libraries by default, and only add new dependencies when clearly justified

Publish or write local issues in dependency order (blockers first).

Apply `ready-for-agent` to each issue.

Set each slice issue to reference the PRD issue as parent. If using local fallback, reference `prd.md` as the parent.

Include machine-readable routing hints in every implementation issue. These hints guide planning only and must not bind runtime orchestration.
State explicitly in each issue that run-with-it remains the final runtime routing authority.

<issue-template>
Use this exact top-level section order for every initial implementation issue body and for local fallback entries in `issues.md`.

## Parent

A reference to the PRD parent issue.

## What to build

A concise end-to-end description of this slice.

## Implementation Steps

Ordered, numbered steps an implementing agent must follow to deliver this slice. Each step must be concrete and actionable:

1. **Step title** — specific action (file to create/modify/delete, function/method/component to add or change, migration to write, test to add). Include exact paths and symbol names derived from codebase exploration.
2. ...

Rules for this section:
- Ordered by execution dependency (earlier steps must not depend on later ones).
- Every step must reference a concrete artifact (file path, function name, API endpoint, schema field, test case name).
- Include at least one test step per slice.
- If a step requires human input or a decision at runtime, flag it: `[HITL]`.
- Do not include steps that belong to other slices.

## Agent Routing

```yaml
agent_routing:
  complexity_hint: <quite-easy|easy|medium|medium-hard|complex|holy-fuck>
  required_capability: <fast|balanced|advanced>
  parallel_safe: <true|false>
  cost_preference: <low|balanced|high>
  speed_preference: <low|balanced|high>
  ownership_scope:
    - <path-or-module-scope>
  verification:
    - <verification-hint>
```

## Technical Context Snapshot

### Current stack in scope

- UI framework and UI libraries currently used
- Backend framework/runtime currently used
- Data layer tooling currently used

### Dependencies in scope

- Existing packages/libraries this slice should reuse
- New dependency additions allowed for this slice: yes/no
- If yes, justification and alternatives considered

### Architecture alignment

- Existing module boundaries to respect
- Existing service/component patterns to follow
- ADRs or architectural constraints to follow
- `create-git-issue` provides routing hints only; it must not assign concrete agent/model names
- `run-with-it` remains the final runtime routing authority

### Integration touchpoints

- APIs/events/contracts affected
- Schemas/migrations/data contracts affected
- Backward compatibility expectations

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Blocked by

- Reference to blocking issue(s), if any

Or "None - can start immediately".

</issue-template>

## Output Checklist

- `gh` availability checked before publishing
- PRD approval captured before parent issue publish attempt
- Issue breakdown approval captured before implementation issue publish attempt
- Each implementation issue includes a technical context snapshot (stack, dependencies, architecture, integration touchpoints)
- Parent/blocked-by relationships set correctly

## Handoff

At completion, report:

- Whether publishing happened via `gh` or local fallback files.
- Parent reference used by implementation slices.
- Which issues are ready for runtime execution by `run-with-it`.
- Any unresolved decisions that block runtime execution.
