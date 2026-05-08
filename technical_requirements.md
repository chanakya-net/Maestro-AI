# Technical Requirements: Skill and Prompt Markdown Audit

## Goal

Review the active Markdown instruction surfaces for the AI-Skills workflow and produce a per-file rewrite plan that identifies where instructions are bolted on, duplicated, misplaced, or unnecessarily verbose.

The audit is requirements/planning work only. It must not rewrite the skill or prompt files during the audit pass.

## Scope

Audit only these files:

- `skills/break-req/SKILL.md`
- `skills/create-git-issue/SKILL.md`
- `skills/run-with-it/SKILL.md`
- `skills/save-tokens/SKILL.md`
- `skills/tdd-implementation/SKILL.md`
- `assets/prompt.md`
- `assets/review-prompt.md`

Out of scope:

- `README.md`
- `docs/**`
- `prd.md`
- `issues.md`
- historical or generated Markdown artifacts
- code, shell scripts, tests, registry JSON, GitHub issue creation, and implementation changes

## Optimization Definition

Optimize for:

1. Agent obedience and correct phase boundaries.
2. Clear ownership of authority and contracts.
3. Token footprint reduction where it does not weaken enforceable behavior.

Do not remove hard stops, output contracts, ownership boundaries, or safety constraints only to shorten the files.

## Responsibility Map

The audit must begin with this cross-file responsibility map and use it to evaluate every file. The map intentionally covers exactly seven production instruction surfaces and must not add `README.md`, tests, scripts, registry JSON, generated issue files, or other docs.

| Scoped file | Target role | Authority boundary |
| --- | --- | --- |
| `skills/break-req/SKILL.md` | Requirements discovery and decision-tree resolution before implementation planning. | May create or update only `technical_requirements.md`; must not implement, create issues, run downstream skills, invoke external coding agents, or proceed past requirements handoff. |
| `skills/create-git-issue/SKILL.md` | Convert resolved requirements into a PRD and dependency-aware implementation issue slices. | Owns PRD synthesis, issue templates, labeling guidance, local `prd.md`/`issues.md` fallback, dependency ordering, and advisory routing hints; must not assign concrete agents/models or execute work. |
| `skills/run-with-it/SKILL.md` | Runtime execution coordinator for ready issues. | Owns issue intake for execution, final agent/model routing, queue/dependency decisions, multi-agent coordination, runner invocation, delegated review lifecycle, persisted run state, status/ledger output, and terminal issue updates. |
| `skills/save-tokens/SKILL.md` | Response compression mode. | Owns wording style for compressed assistant responses only; must not change planning, routing, review, implementation, code blocks, commit messages, PR descriptions, or persisted artifacts. |
| `skills/tdd-implementation/SKILL.md` | Test-first implementation discipline. | Owns red/green/refactor workflow, behavior-first testing rules, and per-cycle implementation checks; must not select issues, route agents/models, or own repo-specific execution orchestration. |
| `assets/prompt.md` | Implementation-agent prompt. | Owns execution guardrails for an already assigned issue and scope; must not perform issue selection, dependency planning, runtime routing, orchestration, or reviewer JSON output. |
| `assets/review-prompt.md` | Reviewer-agent prompt. | Owns read-only review behavior and reviewer JSON artifact requirements; must not edit the working tree, run git or `gh`, update issues, create commits, or emit narrative output after review completion. |

## Source-of-Truth Rule

Repeated contracts must have one conceptual owner.

- Local summaries are allowed for readability.
- Duplicated full contracts should be flagged unless there is a strong obedience reason to keep them inline.
- Any repeated contract must name its authoritative owner or be moved/reworded so the owner is clear.
- Per-file audit slices must classify each duplicate as one of: authoritative owner, permitted local summary, intentional reinforcement, or duplication to rehome/remove.

Likely contracts requiring ownership decisions:

| Contract | Authoritative owner | Downstream slice handling rule |
| --- | --- | --- |
| Reviewer JSON output contract | `assets/review-prompt.md` owns the reviewer artifact shape; `skills/run-with-it/SKILL.md` owns when the reviewer runs, where output is archived, and how verdicts are routed. | Flag duplicated full JSON schemas in coordinator text unless the audit justifies them as obedience-critical; prefer a compact coordinator summary plus clear reference to the review prompt's contract. |
| Implementation test discipline | `skills/tdd-implementation/SKILL.md` owns red/green/refactor and behavior-first testing rules. | Keep short invocations in `assets/prompt.md` or issue templates only as reinforcement; flag copied test-methodology detail outside the TDD skill for tighten/rehome. |
| Routing hints versus final routing authority | `skills/create-git-issue/SKILL.md` owns advisory routing-hint fields in generated issues; `skills/run-with-it/SKILL.md` owns final runtime routing and model/agent selection. | Preserve explicit "advisory only" wording in issue creation surfaces; flag any concrete agent/model assignment outside `run-with-it` for rehome/remove. |
| Terminal status, ledger, and token report formats | `skills/run-with-it/SKILL.md` owns execution-time parseable status lines, final ledgers, token summaries, and terminal issue comment templates. | Flag copies in prompts or issue templates unless they are minimal examples needed by the coordinator; per-file slices should not move these contracts to implementation or review prompts. |
| Resume and persisted state contract | `skills/run-with-it/SKILL.md` owns `.run-with-it/state.json`, review archives, compaction handoff, and resume/discard behavior. | Flag any resume/state rules outside `run-with-it` as rehome/remove unless they are a one-line handoff note. |
| PRD and implementation issue body templates | `skills/create-git-issue/SKILL.md` owns PRD and initial implementation issue body structure. | Keep these templates out of runtime execution prompts except as consumed context; flag duplicated issue-template sections elsewhere for rehome/remove. |

## Audit Strategy

Every scoped file must receive a verdict, but analysis depth should be proportional to risk.

Hotspot files requiring deep passage-level analysis:

- `skills/run-with-it/SKILL.md`
- `skills/create-git-issue/SKILL.md`

Lighter template, trigger, and boundary analysis is sufficient for smaller files unless a conflict is found:

- `skills/break-req/SKILL.md`
- `skills/save-tokens/SKILL.md`
- `skills/tdd-implementation/SKILL.md`
- `assets/prompt.md`
- `assets/review-prompt.md`

## Required File Verdicts

For each scoped file, assign exactly one primary verdict:

- `keep`: already clear and well-scoped
- `tighten`: same behavior, clearer or shorter wording
- `split`: one file carries too many responsibilities
- `rehome`: instruction belongs in another file
- `remove`: obsolete, duplicated, unenforceable, or harmful guidance

The verdict vocabulary is closed: the audit must not introduce any other primary verdict label. The plan may include secondary actions in prose, but the primary verdict must be exactly one of `keep`, `tighten`, `split`, `rehome`, or `remove`.

## Required Per-File Plan Format

For each scoped file, use this standard audit format in the same order:

- current role
- target role
- authority boundary
- primary verdict
- front matter assessment
- passages to keep
- passages to tighten
- passages to move, with destination
- passages to remove
- duplicated contracts and source-of-truth handling
- authority changes, if any
- acceptance checks for the rewrite

Do not produce full rewritten files in the audit output. The audit output is planning only: production skill and prompt files are read-only during the audit and may only be rewritten by a later approved implementation pass.

## Front Matter Requirements

YAML front matter is in scope.

For each skill, check whether `name` and `description`:

- match the skill's actual role and stop point
- avoid overlapping too broadly with other skills
- trigger on the right user intent
- avoid implying authority the body does not own
- are concise enough to avoid noisy activation

## Template Requirements

Future rewrites should use a light standard structure for skill files:

- `Purpose`
- `When To Use`
- `Inputs`
- `Hard Boundaries`
- `Workflow`
- `Outputs`
- `Handoff`

Small mode skills such as `save-tokens` may collapse sections, but the audit must verify those concepts are still covered.

Future rewrites should use a separate prompt-specific structure for shared prompts:

- `Role`
- `Scope`
- `Inputs Expected`
- `Hard Restrictions`
- `Workflow`
- `Verification / Validation`
- `Output Contract`

## Authority Changes

The rewrite plan may recommend authority changes when an instruction belongs in a different file.

Every authority change must name:

- old owner
- proposed new owner
- reason
- expected behavior after the move

Behavior-preserving wording changes do not need authority-change treatment.

## Splitting Policy

The rewrite plan may recommend splitting long machine contracts out of a `SKILL.md` into referenced sub-docs only if the skill runtime can reliably load or include those references.

If reliable loading is uncertain, recommend compact inline appendices instead of external sub-docs.

For `run-with-it`, possible split candidates include:

- routing contract
- status and ledger contract
- review orchestration contract
- resume/state contract
- terminal issue comment contract

The core `SKILL.md` must remain self-contained enough to explain purpose, boundaries, workflow, outputs, and handoff behavior.

## Preliminary Findings To Verify

These are audit seeds, not final conclusions:

- `skills/run-with-it/SKILL.md` appears to be the largest hotspot because it combines routing, execution, review, resume, state persistence, status messages, issue updates, and terminal comment contracts.
- `skills/create-git-issue/SKILL.md` appears to be a hotspot because it combines PRD synthesis, issue publishing, local fallback, issue body templates, technical context snapshots, and routing hints.
- `assets/prompt.md` and `skills/tdd-implementation/SKILL.md` overlap around public-interface testing and behavior-first implementation. The audit should decide whether this is intentional reinforcement or duplication.
- `assets/review-prompt.md` and `skills/run-with-it/SKILL.md` both contain the reviewer JSON contract. The audit should decide which file owns the authoritative version and how summaries should reference it.
- `create-git-issue` and `run-with-it` both mention routing. The audit should ensure `create-git-issue` remains advisory and `run-with-it` remains final authority.

## Per-File Audit Plan: `skills/break-req/SKILL.md`

- current role: Requirements-only discovery skill that interviews the user, optionally explores the codebase to answer factual questions, captures resolved decisions in `technical_requirements.md`, and stops before downstream planning or execution.
- target role: Requirements discovery and decision-tree resolution before implementation planning, matching the responsibility map. The skill should remain the owner of eliciting functional and non-functional requirements and producing the single handoff artifact.
- authority boundary: May create or update only `technical_requirements.md`. Must not implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, invoke external coding agents, or proceed past the requirements handoff.
- primary verdict: `tighten`
- front matter assessment: `name: break-req` is accurate and should stay. The description mostly matches trigger scope, but "audit a tech stack" is broad enough to overlap with architecture review or implementation planning unless constrained by the body. Rewrite should make the trigger explicitly about requirements discovery, dependency mapping, and implementation constraints, with no implied authority to create issues, route agents, or execute work.
- passages to keep:
  - "This skill is requirements-only." Keep as the first body contract because it is short and obedience-critical.
  - The full hard-stop prohibition on implementation, product-file edits, issue creation, GitHub publishing, downstream skill execution, implementation-agent spawning, and external coding-agent invocation. This is the runtime source of truth for the skill's phase boundary.
  - "The only file this skill may create or update is `technical_requirements.md`." Keep as a standalone writable-output boundary.
  - The one-question-at-a-time interview rule and the instruction to answer by exploring the codebase when possible. These define the discovery interface without granting downstream authority.
  - The UI/backend/data coverage checklist, as a concise local checklist for requirements completeness.
  - The final sequence: compile decisions into `technical_requirements.md`, stop, and tell the user they can now run `create-git-issue`.
  - "Do not proceed beyond `technical_requirements.md`, even if the next step is obvious." Keep or strengthen as the closing hard stop.
- passages to tighten:
  - Replace "Recursive tech-audit & decision-tree mapping" with more precise front matter such as requirements discovery, dependency mapping, and technical constraint capture.
  - Rephrase "Interrogate me relentlessly" into a direct workflow instruction. The current wording is forceful but imprecise; the rewrite should preserve thorough questioning without encouraging hostile tone or repeated questioning after a branch is resolved.
  - Group the interview, codebase exploration, layer checklist, output, and handoff under the light standard structure: `Purpose`, `When To Use`, `Inputs`, `Hard Boundaries`, `Workflow`, `Outputs`, `Handoff`.
  - Clarify that codebase exploration is read-only and used only to resolve requirements questions, not to inspect for implementation changes.
  - Clarify that the `create-git-issue` mention is a user handoff, not permission for this skill to invoke that skill.
- passages to move, with destination:
  - None. The skill's current content belongs in `skills/break-req/SKILL.md` because it is local runtime guidance for requirements discovery.
- passages to remove:
  - No functional contracts should be removed. Only subjective or noisy wording should be removed during rewrite if it does not carry enforceable behavior, especially "relentlessly" if replaced by a clearer completeness standard.
- duplicated contracts and source-of-truth handling:
  - Requirements-only boundary: `skills/break-req/SKILL.md` should remain the authoritative runtime owner. The responsibility map in this document is audit guidance, not a replacement for the skill's hard stop.
  - Single writable output boundary: `skills/break-req/SKILL.md` is the runtime source of truth; `tests/break-req-contract.test.sh` is contract verification prior art and should continue checking the exact behavior.
  - Handoff to `create-git-issue`: README owns the high-level workflow summary, while `skills/break-req/SKILL.md` should keep a compact local handoff line so the agent stops and the user knows the next skill. This is intentional reinforcement, not duplication to rehome.
  - UI/backend/data checklist: Local discovery checklist, not a duplicated downstream implementation contract. Keep concise and do not expand into implementation planning.
- authority changes, if any: None. The rewrite should preserve existing authority and make boundaries more explicit; it should not move issue creation, routing, implementation, review, or runtime coordination into this skill.
- acceptance checks for the rewrite:
  - YAML front matter triggers only on requirements discovery, dependency mapping, and technical constraint capture.
  - The body states the skill is requirements-only before any workflow steps.
  - The hard stop still forbids implementation, product-file edits, issue creation, GitHub publishing, `create-git-issue`, `run-with-it`, implementation-agent spawning, and external coding-agent invocation.
  - The only writable output remains exactly `technical_requirements.md`.
  - The handoff tells the user requirements are ready and they can run `create-git-issue`; it does not instruct the agent to run it.
  - The rewrite keeps read-only codebase exploration limited to answering requirements questions.
  - No production file rewrite is included in this audit output.
  - The rewritten skill continues to satisfy `bash tests/break-req-contract.test.sh`.

## Acceptance Criteria

- The audit output covers exactly the seven scoped files.
- The audit begins with a responsibility map.
- Each scoped file has a primary verdict.
- Hotspot files receive passage-level keep/tighten/move/remove recommendations.
- Smaller files receive at least front matter, boundary, trigger, and template-fit analysis.
- Any authority change names old owner, new owner, reason, and expected behavior.
- Any duplicated contract is either assigned a single source of truth or justified as intentional local reinforcement.
- The plan prioritizes agent obedience over token reduction.
- The plan does not rewrite production files directly.
- The plan is sufficient for a future implementation step to rewrite the Markdown without re-interviewing the user.

## Handoff

After this requirements document is complete, the next step is to run `create-git-issue` to turn the audit requirements into a PRD and implementation/audit issues.
