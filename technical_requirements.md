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

## Per-File Audit Plan: `skills/save-tokens/SKILL.md`

- current role: Small response-mode skill that compresses assistant prose by dropping filler, using short wording, and allowing symbolic relationships while preserving exact technical terms and code blocks.
- target role: Response compression mode, matching the responsibility map. The skill should own wording style for compressed assistant responses only, not any planning or execution behavior.
- authority boundary: Owns compressed response wording only. The mode skill has no planning, routing, review, implementation, or persisted-artifact authority, and must not alter code blocks, commit messages, PR descriptions, generated plans, review artifacts, implementation instructions, issue updates, state files, or other durable outputs.
- primary verdict: `tighten`
- front matter assessment: `name: save-tokens` is accurate and should stay. The description correctly states the compression behavior and code-block exception, but the trigger list is too broad: "compress" can mean file compression, prompt compression, memory compression, image compression, or artifact rewriting, and "be brief" can mean concise normal prose rather than activating symbol-heavy mode. Rewrite should retain working response-mode triggers such as "save tokens" and "RTU mode", optionally add "compressed response mode", and drop or qualify generic "compress" and "be brief" activation.
- passages to keep:
  - The core rule to drop articles, filler, pleasantries, hedging, and transitional phrases. It is the simplest enforceable definition of the mode.
  - The preference for short words and sentence fragments. This explains the style without granting process authority.
  - The requirement that technical terms and code blocks remain exact and uncompressed.
  - The before/after React example, if kept short, because it demonstrates the intended compressed reply shape better than abstract rules alone.
  - The explicit exit phrases "stop" and "normal mode", placed under the `Exit` section if the small-mode template is adopted.
- passages to tighten:
  - Make symbol substitution optional or context-aware rather than mandatory for every compressed response, while preserving the current symbol catalog as a compact reference or example. Overuse can reduce clarity, especially in user-facing explanations.
  - Replace the broad trigger words "compress" and "be brief" with scoped trigger wording that does not steal requests meant for file compression, memory compression, prompt rewriting, summaries, or normal concise answers.
  - Expand the boundary line beyond code blocks, commit messages, and PR descriptions to cover formal artifacts: plans, issue bodies, review JSON, test output summaries, implementation prompts, persisted state, and any content intended to be copied or saved.
  - State that compression applies to assistant narration around work, not to quoted source text, user-provided text, command output that needs fidelity, or generated code.
  - Fit the small-mode template by grouping content as `Purpose`, `When To Use`, `Rules`, `Hard Boundaries`, `Example`, and `Exit`, while keeping the file compact.
- passages to move, with destination:
  - None. The response-style rules and exit behavior belong in `skills/save-tokens/SKILL.md` because they are local runtime guidance for this mode.
- passages to remove:
  - Remove or qualify generic activation on "compress" and "be brief" if the rewrite cannot make them unambiguously response-style triggers.
  - Remove any implication that the mode can rewrite durable artifacts or downstream prompts. The current file does not explicitly claim this authority, but the rewrite should avoid adding it through broad wording.
- duplicated contracts and source-of-truth handling:
  - Code-block fidelity: `skills/save-tokens/SKILL.md` should be the local source of truth for how this response mode treats code blocks. Other skills and prompts own their own output contracts; this skill may only say that compression does not modify code blocks.
  - Commit messages and PR descriptions: This skill should keep only a boundary exception. It must not define commit-message or PR-description style; those remain owned by the relevant workflow, prompt, or user request.
  - README token-savings summary: README owns any catalog-level savings summary. The skill should not need a percentage unless it is useful as a non-binding mode description.
  - Formal artifacts: `assets/review-prompt.md`, `skills/run-with-it/SKILL.md`, `skills/create-git-issue/SKILL.md`, and other scoped owners retain their artifact contracts. `skills/save-tokens/SKILL.md` may only preserve them by declaring that compression does not rewrite or symbol-substitute those outputs.
- authority changes, if any: None. The rewrite should preserve the skill's current narrow authority as a response-style mode and explicitly deny planning, routing, review, implementation, and persisted-artifact authority.
- acceptance checks for the rewrite:
  - YAML front matter triggers only on explicit compressed-response intent and does not broadly capture unrelated "compress" or "be brief" requests.
  - The body states compression applies only to assistant response wording.
  - The mode skill has no planning, routing, review, implementation, or persisted-artifact authority.
  - Code blocks, technical terms, commit messages, PR descriptions, formal plans, issue bodies, review artifacts, implementation prompts, command output requiring fidelity, and persisted artifacts remain uncompressed and free of symbol substitution.
  - Exit behavior is explicit and limited to returning assistant responses to normal style.
  - The rewrite fits the small-mode template concepts without bloating the file.
  - No production skill or prompt file rewrite is included in this audit output.

## Per-File Audit Plan: `assets/prompt.md`

- current role: Implementation-only agent prompt for executing an issue already assigned by `run-with-it`. It gives scope discipline, read-before-edit guidance, TDD invocation, implementation guardrails, verification expectations, a pre-completion review checklist, and the final completion report shape.
- target role: Implementation-agent prompt, matching the responsibility map. It should tell an implementer how to execute the assigned scope cleanly after routing has already happened, including expected run context inputs, local exploration, verification, and completion reporting.
- authority boundary: Owns execution guardrails for an already assigned issue and scope. Issue selection, dependency planning, runtime routing, orchestration, and reviewer JSON output stay outside this prompt; `skills/run-with-it/SKILL.md` owns runtime coordination and `assets/review-prompt.md` owns reviewer JSON output.
- primary verdict: `tighten`
- front matter assessment: This shared prompt has no YAML front matter, which is acceptable for a prompt asset rather than a skill trigger. Future rewrite should use the prompt-specific structure from this requirements document: `Role`, `Scope`, `Inputs Expected`, `Hard Restrictions`, `Workflow`, `Verification / Validation`, and `Output Contract`. If metadata is ever added, it must identify this as an implementation-agent prompt only and must not imply issue intake, routing, review, or orchestration authority.
- passages to keep:
  - "This prompt is implementation-only." Keep as the opening contract because it is short and phase-critical.
  - The statement that issue selection, dependency planning, runner selection, and orchestration are handled by `run-with-it`; tighten "runner selection" into runtime routing wording if the rewrite standardizes terminology.
  - The assigned-issue scope bullets: implement only assigned issue(s), keep changes minimal and focused, and avoid unrelated refactors or architecture changes.
  - The exploration-before-code guidance to read nearby code, reuse existing patterns, respect boundaries and dependency direction, and choose the smallest compatible extension when a gap is found.
  - The implementation guardrails around current architecture compatibility, avoiding unnecessary abstractions, preserving API contracts unless requested, and not overwriting unrelated changes.
  - The verification expectation to run issue-specific fast checks first, then broader suites when relevant, and to document omitted expensive checks.
  - The substance of the pre-completion self-check: behavior matches issue intent, naming matches domain language, failure paths are covered, tests validate the right layer, and no unrelated files were changed. Retitle it if needed so it is not confused with delegated reviewer behavior.
  - The completion report fields for files changed, key implementation decisions, checks run with results, and remaining risks or follow-up notes.
  - The `<promise>NO MORE TASKS</promise>` sentinel, but only as an implementation-run completion signal consumed by `run-with-it`, not as queue selection or orchestration authority.
- passages to tighten:
  - Add an `Inputs Expected` section that says the implementer receives an already assigned issue, scope/context, relevant files or constraints when available, and any coordinator-provided next tasks. It should not tell the implementer how to choose or discover new work.
  - Replace "Issue selection, dependency planning, runner selection, and orchestration are handled by the `run-with-it` skill" with a fuller boundary line covering issue selection, dependency planning, runtime routing, orchestration, persisted status/ledger behavior, issue updates, reviewer invocation/lifecycle ownership by `run-with-it`, and reviewer JSON artifact-shape ownership by `assets/review-prompt.md`.
  - Tighten "Invoke `tdd-implementation` first and follow it" into a short source-of-truth invocation that names `skills/tdd-implementation/SKILL.md` as the owner of red/green/refactor and behavior-first test discipline.
  - Reduce the detailed testing bullets "For each behavior, cover both happy path and negative path" and "Test through public interfaces, not internal implementation details" into a compact reinforcement or move them under an explicit "TDD skill owns details" reference. These are useful guardrails but currently overlap with the TDD skill's methodology contract.
  - Clarify that exploration before code is local codebase exploration after assignment, not issue discovery, dependency planning, or runtime queue inspection.
  - Clarify that verification commands are examples to select when applicable, not an exhaustive or mandatory technology matrix for every repo.
  - Clarify that the pre-completion review checklist is a self-check by the implementer, not delegated reviewer behavior and not reviewer JSON output.
- passages to move, with destination:
  - Move any expanded red/green/refactor, happy-path/negative-path, behavior-first, or public-interface testing methodology to `skills/tdd-implementation/SKILL.md` if that detail is missing there. In this prompt, keep only a short invocation plus minimal obedience reinforcement.
  - Move any future wording about queued issue selection, dependency readiness, runtime agent/model choice, persisted state, status ledgers, terminal issue comments, or delegated review lifecycle to `skills/run-with-it/SKILL.md`.
  - Move any future reviewer artifact shape, JSON schema, verdict vocabulary, or read-only review procedure to `assets/review-prompt.md`.
- passages to remove:
  - Remove any wording in a future rewrite that lets the implementation prompt select additional issues, decide dependency order, choose runners, route agents/models, coordinate multiple agents, update GitHub issues, or manage persisted state.
  - Remove any copied full TDD workflow or repeated testing methodology that exceeds a compact invocation of `tdd-implementation`.
  - Remove any reviewer JSON or review-only behavior if it appears during rewrite; the current prompt does not contain reviewer JSON and should stay that way.
  - Remove technology-specific check examples if a future rewrite turns them into universal requirements rather than applicable examples.
- duplicated contracts and source-of-truth handling:
  - Implementation test discipline: `skills/tdd-implementation/SKILL.md` is the authoritative owner of red/green/refactor, behavior-first testing, negative-path coverage, and public-interface testing. The prompt's instruction to invoke `tdd-implementation` is intentional reinforcement. The prompt's detailed happy-path/negative-path and public-interface bullets are duplication to tighten or rehome into the TDD skill if the rewrite needs the detail preserved.
  - Runtime routing and orchestration: `skills/run-with-it/SKILL.md` owns issue intake, dependency decisions, final routing, runner selection, multi-agent coordination, persisted state, status/ledger output, review lifecycle, and terminal issue updates. `assets/prompt.md` may keep only a short boundary reminder that those concerns are already handled before implementation starts.
  - Reviewer JSON output: `assets/review-prompt.md` owns reviewer JSON artifact requirements. `assets/prompt.md` should not define reviewer JSON, review verdicts, or read-only reviewer behavior; its review checklist is only an implementer self-check before completion.
  - Completion reporting: `assets/prompt.md` may own the implementer's local completion summary fields. `skills/run-with-it/SKILL.md` owns any parseable coordinator status, ledger, token report, issue comment, or persisted completion format.
  - No-more-tasks sentinel: `assets/prompt.md` may emit the sentinel when assigned work is complete and no further ready work was provided in context. `skills/run-with-it/SKILL.md` remains the owner of deciding whether more ready work exists and how the sentinel affects orchestration.
- authority changes, if any: None for current behavior. The rewrite should preserve the prompt's implementation-agent authority while making the TDD, coordinator, and reviewer source-of-truth boundaries explicit. Any future methodology expansion should be rehomed from `assets/prompt.md` to `skills/tdd-implementation/SKILL.md`; any future routing or review artifact expansion should be rehomed to `skills/run-with-it/SKILL.md` or `assets/review-prompt.md` respectively.
- acceptance checks for the rewrite:
  - The prompt states that it is implementation-only before workflow details.
  - The prompt adopts the prompt-specific structure: `Role`, `Scope`, `Inputs Expected`, `Hard Restrictions`, `Workflow`, `Verification / Validation`, and `Output Contract`, or explicitly justifies any omitted section.
  - The prompt has an `Inputs Expected` section for already assigned issue context, scope limits, relevant constraints, and coordinator-provided work context.
  - The prompt says issue selection, dependency planning, runtime routing, orchestration, and reviewer JSON output stay outside this prompt.
  - The prompt keeps exploration-before-code guidance limited to reading nearby implementation context and reusing existing codebase patterns after assignment.
  - The prompt invokes `tdd-implementation` as the source of truth for test-first implementation discipline without copying a full red/green/refactor or test-methodology contract.
  - Any overlap with `skills/tdd-implementation/SKILL.md` is classified as intentional reinforcement or duplication to tighten/rehome.
  - Verification rules distinguish applicable fast checks, broader relevant suites, documented omissions for expensive checks, and technology-specific examples that must not become a mandatory matrix for every repo.
  - Completion output remains a concise implementer report and does not define coordinator ledgers, terminal issue comments, or reviewer JSON.
  - The `<promise>NO MORE TASKS</promise>` sentinel remains conditional on assigned work being complete and no further ready work being provided in context.
  - The rewrite does not add issue selection, dependency planning, runtime routing, runner or agent/model selection, orchestration, multi-agent coordination, GitHub issue updating, persisted state, or reviewer JSON authority.

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
