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

## Per-File Audit Plan: `skills/create-git-issue/SKILL.md`

- current role: Hotspot planning-and-publishing skill that turns resolved context into a PRD, creates dependency-aware tracer-bullet implementation issues, applies initial label guidance, writes local fallback artifacts when GitHub publishing is unavailable, and embeds advisory routing hints plus technical context snapshots in each implementation issue.
- target role: Convert resolved requirements into a PRD and dependency-aware implementation issue slices, matching the responsibility map. The skill should remain the owner of PRD synthesis, initial issue body templates, initial label guidance, local `prd.md`/`issues.md` fallback, dependency ordering, and advisory routing metadata before handing execution to `run-with-it`.
- authority boundary: Owns issue creation planning and initial publication only. It may synthesize PRDs, ask for user approval of PRD and slice breakdowns, publish parent and implementation issues with `gh`, or write `prd.md` and `issues.md` as fallback. It must not execute implementation work, select concrete agents or models, run downstream implementation agents, coordinate multi-agent work, manage persisted run state, emit runtime ledgers/status lines, perform delegated review, close issues, or make terminal issue updates. `run-with-it` remains the final runtime routing authority.
- primary verdict: `tighten`
- front matter assessment: `name: create-git-issue` is accurate and should stay. The description correctly captures PRD creation and tracer-bullet issue creation, but "publish everything to the project issue tracker" should be qualified because the body also owns a required local fallback to `prd.md` and `issues.md`. Rewrite the description to trigger on converting resolved requirements, plans, or issue context into a PRD plus implementation issue slices, using GitHub when available and local files when not. The front matter should not imply final runtime routing, concrete agent/model assignment, implementation execution, or terminal issue updates.
- passages to keep:
  - The workflow position list placing `break-req` first, `create-git-issue` second, and `run-with-it` third. Keep the explicit statement that this skill must never claim final routing authority.
  - The canonical label vocabulary with category roles `bug` and `enhancement`, state roles `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`, plus the rule that each published issue gets exactly one category role and one state role.
  - The label mapping rule for trackers with different label strings, including one focused clarification question when the mapping is ambiguous.
  - The GitHub CLI preflight check and body-file publishing policy. Keeping body files is important because the skill owns multiline Markdown issue bodies.
  - The policy that GitHub publishing failures fall back to local files rather than retrying through another GitHub integration.
  - The local fallback contract requiring exactly two workspace-root files: `prd.md` for the parent PRD body and `issues.md` for every approved implementation slice in dependency order.
  - The fallback details requiring title, intended labels, parent relationship, technical context snapshots, acceptance criteria, blocked-by content, and the `prd.md` local parent reference.
  - The `break-req` artifact reuse priority order and the rule not to ask the user to repeat decisions already resolved in `technical_requirements.md` or conversation history.
  - The instruction to synthesize a PRD before interviewing the user. This keeps the skill from re-running `break-req` and makes review gates delta-focused.
  - The PRD template sections and the restriction that implementation decisions in the PRD should not include file paths or code snippets.
  - The parent PRD issue publishing step, including `enhancement` and `needs-triage` labels and capturing the parent issue URL or number for implementation slices.
  - The exact implementation issue top-level heading order: `## Parent`, `## What to build`, `## Agent Routing`, `## Technical Context Snapshot`, `## Acceptance criteria`, and `## Blocked by`.
  - The tracer-bullet slice rules requiring narrow end-to-end slices that are independently verifiable, demoable, dependency-aware, and preferably many thin slices over few thick slices.
  - The user issue-breakdown review gate covering granularity, dependencies, merge/split decisions, and HITL/AFK assignment.
  - The technical context snapshot requirements for stack, dependencies, architecture alignment, integration touchpoints, dependency policy, and reusable existing libraries.
  - The machine-readable `agent_routing` YAML block as advisory planning metadata, including complexity hint, capability hint, parallel-safety hint, cost/speed preferences, ownership scope, and verification hints.
  - The explicit advisory-only routing language: the skill provides routing hints only, must not assign concrete agent/model names, and `run-with-it` remains the final runtime routing authority.
  - The output checklist requirement that `gh` availability is checked, technical snapshots are included, and parent/blocked-by relationships are set correctly.
- passages to tighten:
  - Reorganize the body under the standard skill structure: `Purpose`, `When To Use`, `Inputs`, `Hard Boundaries`, `Workflow`, `Outputs`, and `Handoff`. The current body has the right contracts but interleaves policy, workflow, templates, and handoff details.
  - Tighten the opening workflow position into an enforceable phase boundary: this skill consumes `break-req` outputs when present and hands off ready issues or local issue files to `run-with-it`; it does not invoke either downstream skill.
  - Clarify that codebase exploration is read-only and used to produce accurate PRDs, technical context snapshots, labels, dependency ordering, and issue templates. It should not inspect with intent to implement or modify production files.
  - Make the PRD review gate explicit before publishing the parent issue. The current text says to check deltas with the user, but the publish step should state that the PRD must be approved or only needs no-op confirmation before `gh issue create` or `prd.md` fallback output.
  - Keep the issue-breakdown review gate explicit before publishing implementation issues. The rewrite should make approval of slice titles, dependencies, and HITL/AFK classification a hard gate, not only a quiz.
  - Clarify partial publishing behavior. If the PRD issue is created but an implementation issue publish fails, the skill should stop creating more GitHub issues, write the complete approved plan to `prd.md` and `issues.md` with any already-published issue references included, and tell the user exactly what was published and what was written locally.
  - Clarify local fallback idempotence. The rewrite should say whether existing `prd.md` or `issues.md` are overwritten only after confirmation, regenerated from the current approved plan, or treated as stale outputs to replace. The current "create exactly two files" contract is strong but does not define collision handling.
  - Tighten the `gh` policy to check availability and repository inference before publishing, use body files for every multiline issue, and avoid alternate GitHub integrations unless a later skill explicitly owns them.
  - Clarify that canonical labels are planning labels for newly created PRD and slice issues. Runtime state transitions, closing, terminal comments, and ledger-related issue updates belong to `run-with-it`.
  - Add a compact definition or reference for HITL versus AFK assignment because the skill asks the user to validate those assignments but does not define the terms locally.
  - Keep the top-level issue template order as a hard contract, but move the duplicated order statement and full template closer together so future rewrites do not accidentally diverge.
  - Strengthen the `Agent Routing` section so every generated issue states in prose, outside the YAML block, that routing hints are advisory, concrete agent/model assignment belongs outside this skill, and `run-with-it` is the final runtime routing authority.
  - Tighten `complexity_hint` usage to the closed labels already consumed by `run-with-it` without copying the full deterministic router. The issue should carry the hint, not the scoring algorithm.
  - Require technical context snapshots to cite observable repo facts when possible and mark unknowns explicitly instead of inventing stack details.
  - Expand the output checklist to include PRD approval, issue-breakdown approval, publishing or fallback result, exact template order, advisory routing language, technical snapshot completeness, dependency order, and the handoff to `run-with-it`.
  - Add a final handoff section that tells the user what was created, where local fallback files are if used, which issues are ready for `run-with-it`, and which decisions remain unresolved. The handoff must not start execution.
- passages to move, with destination:
  - Move any future concrete agent/model selection rules, model catalogs, score-to-weight tables, fallback budgets, parallel-agent coordination, or route line formats to `skills/run-with-it/SKILL.md`. Keep only the compact advisory routing-hint schema in this skill.
  - Move any future runtime status, final ledger, token telemetry, persisted `.run-with-it/state.json`, resume/discard, review-cycle, or terminal issue-update contracts to `skills/run-with-it/SKILL.md`.
  - Move any future reviewer JSON schema, read-only review behavior, or delegated review verdict handling to `assets/review-prompt.md` or `skills/run-with-it/SKILL.md`, depending on whether the content is reviewer artifact shape or coordinator lifecycle.
  - Move any expanded red/green/refactor, happy-path/negative-path, or public-interface testing methodology to `skills/tdd-implementation/SKILL.md`. Implementation issues may include acceptance criteria and verification hints, but the methodology source of truth stays in the TDD skill.
  - Move any requirements-interview decision-tree behavior that goes beyond resolving missing PRD or publishing details to `skills/break-req/SKILL.md`. `create-git-issue` should ask only focused follow-ups needed to publish accurate issues.
- passages to remove:
  - Remove or reword front matter and body phrases that imply GitHub publishing is mandatory when local fallback is a first-class contract.
  - Remove any future wording that lets this skill choose concrete agents or models, execute work, spawn implementation agents, coordinate multi-agent batches, manage review cycles, close issues, or update terminal issue status.
  - Remove duplicated full routing algorithms if they are added during rewrite. The generated issues only need advisory fields and a pointer that final routing belongs to `run-with-it`.
  - Remove speculative technical context not grounded in codebase evidence, user-approved requirements, or explicit unknown markers.
  - Remove any extra top-level sections in the implementation issue template. New detail must live under the six approved headings unless the template contract is intentionally changed in a later requirements pass.
  - Remove any repeated PRD or issue template fragments that can drift from the authoritative template in this skill.
- duplicated contracts and source-of-truth handling:
  - PRD synthesis and initial implementation issue body templates: `skills/create-git-issue/SKILL.md` is the authoritative owner. Other prompts and skills may consume generated issue bodies but should not duplicate or redefine the parent PRD or implementation issue template.
  - Routing hints versus final routing authority: `skills/create-git-issue/SKILL.md` owns advisory routing-hint fields in generated issues. `skills/run-with-it/SKILL.md` owns final runtime routing, model/agent selection, queue decisions, fallback behavior, and execution. The current advisory wording is intentional reinforcement and should be kept; any concrete agent/model assignment outside `run-with-it` should be removed.
  - Complexity labels: `create-git-issue` may use the closed `complexity_hint` labels as issue metadata because `run-with-it` consumes those labels. The deterministic scoring and model-weight mapping remain owned by `run-with-it` and should not be copied into this skill.
  - Label vocabulary: Within the scoped audit, `skills/create-git-issue/SKILL.md` owns the initial category/state label guidance for PRD and slice creation. Later runtime status updates, terminal comments, and issue closure behavior belong to `skills/run-with-it/SKILL.md`.
  - GitHub CLI publishing: `create-git-issue` owns initial PRD and implementation issue creation with `gh issue create` and body files. `run-with-it` owns execution-time issue intake and terminal issue updates. Do not merge these policies.
  - Local fallback: `create-git-issue` owns generation of `prd.md` and `issues.md`. `run-with-it` may consume local `issues.md` as an intake fallback, but it should not own the authoring template or PRD synthesis rules.
  - Technical context snapshot: `create-git-issue` owns the initial snapshot embedded in each issue. Implementation agents may verify or update code during execution, but they should not redefine the required snapshot structure.
  - User review gates: `create-git-issue` owns PRD approval and issue-breakdown approval before publishing. `run-with-it` owns delegated review after implementation. These are different review gates and should stay named distinctly.
  - TDD and implementation verification: `skills/tdd-implementation/SKILL.md` owns test-first implementation discipline. `create-git-issue` may include acceptance criteria and verification hints in issues as intentional reinforcement, not as a copied testing methodology contract.
- authority changes, if any: None. The rewrite should preserve `create-git-issue` as the owner of PRD synthesis, initial issue templates, publishing/fallback, label guidance, dependency ordering, and advisory routing hints while making the downstream boundary with `run-with-it` more explicit.
- acceptance checks for the rewrite:
  - YAML front matter triggers on PRD synthesis and implementation issue creation from resolved requirements, plans, or issue context, and it mentions local fallback or avoids implying GitHub publishing is always available.
  - The body clearly places the skill after `break-req` and before `run-with-it`, without instructing the agent to invoke either skill.
  - The hard boundaries say this skill must not execute implementation work, select concrete agents or models, coordinate multi-agent execution, manage review cycles, persist `.run-with-it` state, emit runtime ledgers/status lines, close issues, or perform terminal issue updates.
  - Canonical label vocabulary remains exactly scoped to one category role and one state role per new issue, with clear mapping behavior for trackers that use different label strings.
  - GitHub publishing uses `gh issue create` with body files after availability/repository checks, and failures use the documented local fallback rather than another GitHub integration.
  - Local fallback still creates exactly `prd.md` and `issues.md` in the workspace root, preserving approved titles, labels, parent references, section order, routing hints, technical snapshots, acceptance criteria, and blocked-by content.
  - Existing `break-req` artifacts are reused before asking follow-up questions, and follow-ups are limited to unresolved, contradictory, or missing decisions needed for publication.
  - PRD synthesis happens before user questioning, and PRD approval is required before publishing the parent PRD issue or writing `prd.md`.
  - Implementation issue breakdown approval is required before publishing slice issues or writing `issues.md`.
  - Every initial implementation issue uses the exact top-level section order: `## Parent`, `## What to build`, `## Agent Routing`, `## Technical Context Snapshot`, `## Acceptance criteria`, `## Blocked by`.
  - Tracer-bullet slice rules remain end-to-end, dependency-aware, independently verifiable, and as thin as practical.
  - Technical context snapshots cover stack, dependencies, architecture alignment, integration touchpoints, and dependency policy, with unknowns marked rather than invented.
  - Every implementation issue includes machine-readable routing hints that remain advisory planning metadata only.
  - The issue body states that concrete agent/model assignment belongs outside this skill and `run-with-it` remains the final runtime routing authority.
  - The output checklist covers publish/fallback status, PRD and issue approval gates, labels, parent/blocked-by links, technical snapshot completeness, routing-advisory language, and handoff to `run-with-it`.
  - No production skill or prompt file rewrite is included in this audit output.

## Per-File Audit Plan: `assets/review-prompt.md`

- current role: Review-only prompt asset consumed by `run-with-it` after an implementation or modification diff exists. It instructs a reviewer agent to inspect provided task context and diff, treat the repository as read-only, avoid all mutation and GitHub operations, and write exactly one JSON reviewer artifact at a coordinator-provided path.
- target role: Reviewer-agent prompt, matching the responsibility map. It should own read-only reviewer behavior, expected reviewer inputs, review decision rules, and the reviewer JSON artifact shape. It should not own when review runs, reviewer model selection, review cycle counting, archive destination, verdict routing, modification-agent spawning, persisted state, status/ledger output, terminal issue updates, commits, or any working-tree mutation.
- authority boundary: Owns the reviewer agent's local obligations only: read provided context and diff, apply approval/revision/rejection rules, and write the required JSON file to the output path supplied by the coordinator. `skills/run-with-it/SKILL.md` owns the coordinator lifecycle around that artifact, including assembling the payload, selecting and spawning the reviewer, parsing and archiving JSON under `.run-with-it/reviews/`, routing `approve`/`revise`/`reject`, spawning modification agents, committing accepted work, and updating issues.
- primary verdict: `tighten`
- front matter assessment: This prompt asset has no YAML front matter, which is appropriate because it is not a skill trigger and should not activate from user intent. A rewrite should not add skill-style front matter unless prompt assets adopt metadata repo-wide. The prompt should instead use the prompt-specific structure from this requirements document: `Role`, `Scope`, `Inputs Expected`, `Hard Restrictions`, `Workflow`, `Verification / Validation`, and `Output Contract`.
- passages to keep:
  - "# Review Prompt" or an equivalent `Role` section naming the reviewer-agent role.
  - "This prompt is review-only guidance for `run-with-it`." Keep the dependency direction, but tighten it to say the reviewer consumes coordinator-provided inputs and does not coordinate the run.
  - The scope bullets requiring review of the provided implementation diff and task context, validation against issue requirements and acceptance criteria, and production of exactly one JSON file.
  - Runtime assumptions that the reviewer uses the same OS and path-handling assumptions as `prompt.md` when interpreting platform-specific paths.
  - The repository-as-read-only assumption.
  - All hard restrictions: no working-tree edits, no `git`, no `gh`, no issue updates, no commits, no branches, no tags, and no narrative/status/markdown output after review completion.
  - The JSON output fields: `verdict`, `summary`, `comments`, and `blocking_reasons`.
  - The closed reviewer verdict vocabulary: `approve`, `revise`, and `reject`.
  - The review rules for when to approve, request revision, or reject, because they give the reviewer enforceable decision boundaries.
  - "The JSON file is the only required artifact." Keep as the no-narrative-output reinforcement.
- passages to tighten:
  - Add an `Inputs Expected` section that lists coordinator-provided issue/task context, original implementation prompt context, implementation or modification diff, changed-file summary with line counts when available, telemetry stub if present, and the mandatory output path for the reviewer JSON.
  - Replace "if the coordinator provides an output path" with mandatory wording. The reviewer should write to the supplied output path and must not invent a default path, archive location, or issue comment destination.
  - Clarify that same-OS/path assumptions are for interpreting provided paths and writing the JSON output file only; they are not permission to run shell probes, `git`, `gh`, or filesystem mutation outside the output artifact.
  - Tighten repository read-only language so reading provided payload and source files is allowed, while editing, formatting, checkout/reset, generated-file refreshes, dependency installs that mutate files, and any repository writes are forbidden.
  - Organize the prompt under `Role`, `Scope`, `Inputs Expected`, `Hard Restrictions`, `Workflow`, `Verification / Validation`, and `Output Contract`.
  - Clarify workflow order: inspect task requirements and acceptance criteria, inspect the diff and changed-file summary, validate behavior and verification evidence, produce JSON, then stop.
  - Clarify that `comments` entries should use repo-relative file paths, concrete line numbers when the finding is line-specific, closed severity values `info`, `warning`, or `critical`, and a concrete `fix` string. Allow an empty comments array for a clean approval if no actionable findings remain.
  - Clarify `blocking_reasons`: keep the field mandatory in the JSON shape, require it to be non-empty when `verdict` is `reject`, and require an empty array for `approve` or `revise`.
  - Clarify that `summary` is a concise rationale inside the JSON, not narrative output printed after the artifact is written.
  - Clarify that `revise` means targeted fixes are likely sufficient within the current issue scope, while `reject` means the work is fundamentally off-scope, unsafe, or not repairable through a small modification cycle.
  - Tighten the internal-only note so it does not imply the reviewer controls archival or consumption. The artifact is internal to the coordinator; `run-with-it` decides where to archive it and how to route its verdict.
- passages to move, with destination:
  - Move any future wording about when reviewers run, reviewer band/model selection, cycle caps, degraded review fallback, review-spawn or review-result status lines, modification-agent spawning, archive paths, terminal issue comments, commits, queue state, or `.run-with-it/state.json` to `skills/run-with-it/SKILL.md`.
  - Move any future implementation guidance, code-edit instructions, TDD workflow detail, verification command selection for implementers, or completion-report format to `assets/prompt.md` or `skills/tdd-implementation/SKILL.md` depending on whether it is implementation prompt guidance or test-discipline methodology.
  - Move any future issue creation, PRD, initial issue template, label, or advisory routing-hint guidance to `skills/create-git-issue/SKILL.md`.
- passages to remove:
  - Remove the conditional implication in "If the coordinator provides an output path"; the output path should be a required input.
  - Remove any future permission to run `git`, call `gh`, edit files, update issues, create commits/branches/tags, post markdown summaries, or print status after completion.
  - Remove any future coordinator lifecycle detail that duplicates `run-with-it`, especially review cycle counting, archival paths, status/ledger formats, verdict routing, modification-agent behavior, terminal issue updates, or commit policy.
  - Remove any duplicate full implementation prompt or TDD methodology if it appears during rewrite; the reviewer should evaluate implementation quality, not become an implementer.
- duplicated contracts and source-of-truth handling:
  - Reviewer JSON artifact shape: `assets/review-prompt.md` should remain the authoritative owner of the reviewer JSON fields, reviewer verdict vocabulary, comment object shape, blocking-reasons requirement, and no-narrative artifact behavior.
  - Coordinator review lifecycle: `skills/run-with-it/SKILL.md` is the authoritative owner of when the reviewer runs, what payload is assembled, which model/agent is selected, cycle counting, degraded fallback, where the JSON is archived, how verdicts are routed, whether a modification agent runs, and terminal issue updates.
  - JSON schema duplication in `skills/run-with-it/SKILL.md`: the current full schema copy in the review handoff JSON contract area, around lines 613-628 in the current file, is duplicated against the review prompt. A future `run-with-it` rewrite should either keep only a compact parse/validation summary that explicitly references `assets/review-prompt.md` as the source of truth, or justify a small inline mirror as obedience-critical for coordinator parsing. The authoritative artifact shape should not move out of the review prompt.
  - Output path ownership: `run-with-it` owns supplying the output path and later archiving the parsed JSON. `assets/review-prompt.md` owns the reviewer obligation to write exactly one JSON artifact at that supplied path and stop.
  - Read-only restrictions: `assets/review-prompt.md` is the reviewer-runtime source of truth for no edits, no `git`, no `gh`, no issue updates, no commits/branches/tags, and no narrative output. `run-with-it` may summarize those restrictions when spawning reviewers, but should not broaden reviewer authority.
  - Verdict handling: `assets/review-prompt.md` owns how a reviewer chooses `approve`, `revise`, or `reject`. `skills/run-with-it/SKILL.md` owns what the coordinator does after each verdict.
- authority changes, if any: None. The rewrite should preserve `assets/review-prompt.md` as the owner of read-only reviewer behavior and reviewer JSON artifact requirements, while making the boundary with `run-with-it` explicit. Any future coordinator lifecycle or archive-path detail should be rehomed to `skills/run-with-it/SKILL.md`; that is a clarification of existing ownership, not a change to reviewer authority.
- acceptance checks for the rewrite:
  - The prompt remains a prompt asset with no YAML front matter unless prompt metadata is adopted consistently across assets.
  - The prompt uses or clearly maps to the prompt-specific structure: `Role`, `Scope`, `Inputs Expected`, `Hard Restrictions`, `Workflow`, `Verification / Validation`, and `Output Contract`.
  - `Inputs Expected` names coordinator-provided task context, implementation or modification diff, changed-file summary when available, verification evidence when present, and a mandatory reviewer JSON output path.
  - The prompt states the repository is read-only input and forbids edits, formatting writes, generated-file refreshes, dependency-install writes, `git`, `gh`, issue updates, commits, branches, and tags.
  - Same-OS/path assumptions are limited to interpreting provided paths and writing the JSON artifact at the supplied output path.
  - The prompt requires exactly one JSON artifact and no narrative, status text, or markdown after review completion.
  - The JSON contract includes `verdict`, `summary`, `comments`, and `blocking_reasons`, with reviewer verdicts limited to `approve`, `revise`, and `reject`.
  - The comments contract specifies repo-relative file paths, line numbers when applicable, severity values `info`, `warning`, or `critical`, and concrete fix text.
  - The blocking-reasons rule is explicit for `reject` and unambiguous for `approve` and `revise`.
  - Review rules distinguish `approve`, `revise`, and `reject` by issue satisfaction, targeted fixability, and off-scope or unsafe failure.
  - The prompt says `run-with-it` owns reviewer scheduling, model/agent selection, cycle counting, JSON archival, verdict routing, modification-agent spawning, terminal issue updates, persisted state, status/ledger output, and commits.
  - Any duplicated reviewer JSON schema in `run-with-it` is treated as a coordinator parse summary or duplication to tighten, not as the authoritative artifact owner.
  - No production skill or prompt file rewrite is included in this audit output.

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
