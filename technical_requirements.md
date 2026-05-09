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

## Per-File Audit Plan: `skills/tdd-implementation/SKILL.md`

- current role: Test-first implementation discipline skill that defines red/green/refactor, behavior-first testing through public interfaces, positive and negative path expectations, vertical tracer-bullet sequencing, green-only refactoring, stack alignment, dependency discipline, and a per-cycle checklist.
- target role: Test-first implementation discipline, matching the responsibility map. The skill should own how an implementer tests and evolves an already selected feature or bugfix slice, especially red/green/refactor and observable-behavior testing, without selecting issues, routing agents/models, coordinating execution, or owning repo-specific runtime queue behavior.
- authority boundary: Owns implementation-time TDD methodology only. It may guide test planning for assigned work, public-interface test design, per-behavior positive and negative coverage, minimal implementation, green-only refactors, and tech-stack alignment. It must not own issue selection, dependency readiness, final routing, runner/model selection, multi-agent orchestration, delegated review, GitHub updates, persisted run state, commits, terminal ledgers, or broader product planning.
- primary verdict: `tighten`
- front matter assessment: `name: tdd-implementation` is accurate and should stay. The description correctly triggers on TDD, test-first implementation, and stronger integration tests, but "build features or fix bugs using TDD" should be scoped to implementation discipline for assigned work so it does not imply broad feature-planning, issue-selection, or orchestration authority. Rewrite should keep activation on explicit TDD/test-first requests and implementation-prompt invocation, while clarifying that the skill governs test methodology, not runtime coordination or queue planning.
- passages to keep:
  - "Use a strict red-green-refactor cycle with thin vertical slices." Keep as the opening methodology contract.
  - The philosophy that tests should validate behavior through public interfaces rather than implementation details. This is the core source of truth for public-interface testing.
  - The good-test and bad-test examples around public APIs, specification-like assertions, refactor resilience, excessive internal mocking, private methods, and internal call order.
  - The horizontal-slice anti-pattern and vertical tracer-bullet examples. They are short, enforceable, and directly preserve the thin vertical-slice rule.
  - The first tracer-bullet sequence: write one failing test, confirm red, write minimal code, confirm green, then add the matching negative-path test for the same behavior.
  - The incremental loop rules: one test at a time, positive and negative path coverage before moving on, no speculative features, and assertions on observable behavior only.
  - "Never refactor while red." Keep as the green-only refactor rule, along with the requirement to re-run tests after refactor steps.
  - Tech-stack alignment rules to reuse existing test framework, assertion style, package ecosystem, architecture, and module boundaries, and to add dependencies only with explicit justification.
  - The per-cycle checklist, especially behavior-not-implementation, public interface only, refactor resilience, minimal code, positive and negative coverage, and no speculative features.
- passages to tighten:
  - Rewrite "Plan with user" into assigned-work test-plan calibration. The current wording can imply broad planning authority and mandatory user approval; the rewrite should instead say to derive the test plan from the assigned issue/context, confirm public interface or behavior ambiguities only when blocking, and avoid selecting or reshaping work outside the assigned slice.
  - Clarify that identifying deep modules, architecture constraints, and ADRs is read-only implementation alignment for the current slice, not architecture discovery with authority to re-plan the project.
  - Tighten "stronger integration tests" in the front matter so it triggers behavior-first implementation testing, not generic test-audit or coverage-improvement work unrelated to an assigned implementation.
  - Add explicit no-orchestration boundary language near the top: this skill does not select issues, route agents/models, manage queues, coordinate parallel agents, run delegated review, update GitHub, create commits, or own `.run-with-it` state.
  - Rename or restructure sections to fit the light skill template: `Purpose`, `When To Use`, `Inputs`, `Hard Boundaries`, `Workflow`, `Outputs`, and `Handoff`, while preserving the existing concise examples.
  - Make the negative-path rule cover invalid input, rejected state, permission failure, boundary violation, and error paths as examples rather than an exhaustive mandatory list for every behavior.
  - Clarify that dependency additions require explicit justification against existing stack options and must follow the assigned issue's architecture constraints.
  - Add a final handoff/output expectation that the implementer reports tests/checks run and any coverage limits, without taking over coordinator ledgers or issue-update formats.
- passages to move, with destination:
  - Move no current passages out as mandatory rehomes. The testing methodology belongs in `skills/tdd-implementation/SKILL.md`.
  - If a future rewrite expands issue selection, dependency readiness, runtime routing, runner/model choice, queue behavior, multi-agent coordination, review lifecycle, status/ledger output, commits, or terminal issue updates, move that content to `skills/run-with-it/SKILL.md`.
  - If a future rewrite expands implementation-agent scope discipline, completion-report fields, or generic assigned-issue guardrails beyond TDD methodology, move that content to `assets/prompt.md`.
  - If a future rewrite adds initial issue acceptance-criteria templates or technical-context snapshot requirements, move that content to `skills/create-git-issue/SKILL.md`.
- passages to remove:
  - Remove any future wording that makes user approval of a test plan mandatory in unattended implementation-agent runs. Keep a blocker-only clarification rule instead.
  - Remove or reword any implication that this skill can choose what issue to work on, reprioritize the queue, assign models/agents, coordinate multiple implementers, run delegated review, post GitHub updates, create commits, or manage runtime state.
  - Remove any copied implementation prompt completion format, reviewer JSON contract, routing table, status ledger, or issue template if added during rewrite.
  - Remove any testing guidance that encourages private-method assertions, internal call-order assertions, excessive internal mocking, speculative coverage, or broad horizontal test batches before implementation.
- duplicated contracts and source-of-truth handling:
  - Implementation test discipline: `skills/tdd-implementation/SKILL.md` is the authoritative owner of red/green/refactor, public-interface testing, behavior-first assertions, positive and negative path coverage, vertical tracer-bullet sequencing, green-only refactoring, and per-cycle TDD checks.
  - `assets/prompt.md` overlap: The implementation prompt may intentionally invoke `tdd-implementation` and keep compact reinforcement that each behavior needs positive and negative coverage and public-interface tests. Full TDD workflow detail in `assets/prompt.md` is duplication to tighten or rehome; the authoritative methodology stays in this skill.
  - Vertical-slice language: `skills/tdd-implementation/SKILL.md` owns vertical sequencing inside the implementation cycle. `skills/create-git-issue/SKILL.md` owns dependency-aware issue slicing and initial issue templates. The shared "tracer-bullet" vocabulary is permitted local reinforcement when each file's stop point is explicit.
  - Tech-stack alignment and dependency policy: `skills/tdd-implementation/SKILL.md` owns per-cycle implementation discipline to reuse the repo's test framework, package ecosystem, and architecture. `assets/prompt.md` may keep broader implementation guardrails, and `skills/create-git-issue/SKILL.md` owns initial technical-context snapshots and dependency-policy hints in generated issues.
  - Runtime coordination: `skills/run-with-it/SKILL.md` remains the source of truth for issue selection, dependency readiness, final routing, model/agent selection, multi-agent coordination, delegated review, status/ledger output, persisted state, commits, and terminal issue updates. `skills/tdd-implementation/SKILL.md` should name this boundary only as a hard stop.
- authority changes, if any: None. The rewrite should preserve current TDD methodology ownership while removing any ambiguity from "Plan with user" and front matter trigger wording. The skill should become narrower and clearer, not gain planning, routing, orchestration, review, GitHub, commit, or runtime-state authority.
- acceptance checks for the rewrite:
  - YAML front matter name remains `tdd-implementation`.
  - YAML description triggers on explicit TDD/test-first implementation discipline and implementation-prompt invocation, without implying broad planning, issue selection, routing, or orchestration authority.
  - The body states the skill owns red/green/refactor and behavior-first testing for assigned implementation work before workflow details.
  - The body says tests should exercise public interfaces and observable behavior, not private methods, internal call order, or excessive internal mocks.
  - Each behavior is handled as a vertical tracer bullet: failing test, minimal implementation to green, matching negative path, then next behavior.
  - Positive and negative path expectations remain explicit, with invalid input, rejected state, permission failure, boundary violation, and error path treated as examples of negative coverage.
  - Refactoring is allowed only while tests are green, and tests are re-run after refactor steps.
  - Tech-stack alignment requires existing test framework, assertion style, package ecosystem, architecture, and module boundaries, with new dependencies allowed only with explicit justification.
  - The per-cycle checklist remains present and checks behavior focus, public-interface testing, refactor resilience, minimal code, positive and negative coverage, and no speculative features.
  - Any overlap with `assets/prompt.md` is classified as intentional invocation/reinforcement or duplication to tighten/rehome, with `skills/tdd-implementation/SKILL.md` as the authoritative methodology owner.
  - The rewrite adds no issue selection, dependency readiness, runtime routing, runner/model selection, multi-agent orchestration, delegated review, GitHub update, commit, persisted-state, status-ledger, or repo-specific queue behavior authority.
  - No production skill or prompt file rewrite is included in this audit output.

## Per-File Audit Plan: `skills/run-with-it/SKILL.md`

- current role: Largest runtime coordination hotspot. The file currently owns ready-issue intake, OS and asset discovery, no-git fallback behavior, deterministic complexity scoring, model-first routing, registry filtering, provider fallback, multi-agent coordination, runner invocation, delegated review orchestration, persisted run state, status and ledger output, token telemetry summaries, terminal issue updates, and commit/closure policy.
- target role: Runtime execution coordinator for ready issues, matching the responsibility map. It should remain the final authority for execution-time issue intake, final agent/model routing, queue and dependency decisions, safe parallelization, runner invocation, delegated review lifecycle, persisted state and resume behavior, status/ledger/token output, terminal issue comments, issue closure, and per-issue commits. The rewrite should make those responsibilities easier to obey by separating the core workflow from compact inline contracts.
- authority boundary: Owns runtime coordination after issues or local issue files already exist. It may consume advisory routing hints from `create-git-issue`, but final runtime routing authority remains with `run-with-it`. It may call the unified runner with selected `AGENT` and `MODEL`, but `run-agent.sh` and `run-agent.ps1` own execution mechanics only. It may assemble implementation and reviewer payloads, but `assets/prompt.md` owns implementation-agent guardrails and `assets/review-prompt.md` owns read-only reviewer behavior plus reviewer JSON artifact shape. It must not synthesize PRDs, author initial issue templates, change registry data, or rewrite runner scripts.
- primary verdict: `split`
- front matter assessment: `name: run-with-it` is accurate and should stay. The description correctly says the skill routes issue-running automation through a deterministic control plane, selects agent/model targets from the registry, can coordinate safe parallel agents, and uses the unified runner. Tighten it to mention ready issues and runtime execution coordination explicitly. Avoid implying that this skill creates PRDs or initial issue templates. Also avoid saying routing is fully deterministic if the body keeps randomized model or interchangeable-agent selection; a better description is deterministic scoring plus registry-constrained selection.
- passages to keep:
  - The preferred upstream flow that places `break-req`, `create-git-issue`, and then `run-with-it` in order. Keep it near the top because it preserves the workflow position after `create-git-issue`.
  - The OS detection concept and the platform command table. Keep the behavior, but make it a compact runtime prerequisite rather than a large early detour.
  - Asset discovery order: `ASSETS_DEST`, `$HOME/.ai-skill-collections/assets`, then `./assets`. Keep filesystem-based discovery and the rule that the resolved asset root is the source for the run.
  - Fresh/no-git project support: no git required for asset discovery, no-git should continue with empty commit context, and `.gitignore` auto-append must be skipped when `.git/` is absent.
  - The responsibility boundary saying `run-with-it` owns issue intake, complexity scoring, routing, multi-agent planning, fallback, status, and routing reports, while `prompt.md`, `review-prompt.md`, and the runner scripts have narrower roles.
  - The multi-agent capability rules for when parallel execution is safe versus when sequential execution is required. Keep one main coordinator responsible for queue decisions, integration, verification, commits, and issue updates.
  - Issue intake priority: use provided issue data first, fetch with `gh` when needed, fall back to local `issues.md`, and tolerate unavailable git metadata.
  - The eight scoring dimensions and the `8-40` score range. Keep the hard minimum overrides and the registry-backed score-to-weight mapping.
  - Model-first routing and explicit `AGENT` plus `MODEL` runner invocation. Keep the rule that agent defaults are ignored for normal routed runs.
  - Provider fallback policy that keeps Google/Gemini last-resort-only for automatic routing while allowing explicit user constraints to force it after validation.
  - Override precedence, allowlist/denylist behavior, and bounded fallback diagnostics. These are runtime coordination contracts and belong here.
  - Preflight checks, especially asset root, prompt, review prompt, runner, registry, `gh` auth when needed, runner support, review-band reachability, and existing-state detection before issue intake.
  - The parseable `ROUTE|...` summary, human-readable routing details, `STATUS|...` lines, final ledger rows, token telemetry fields, and final human-readable token summary sections.
  - Context-budget and compaction handoff behavior, including the 50% threshold, user-driven compaction only, state persistence, and `STATUS|type=compact|...`.
  - Persistent `.run-with-it/state.json`, review archive paths, resume/discard prompt, rehydration categories, in-flight reattempt rules, and restored review-cycle cap behavior.
  - `.gitignore` auto-append for `.run-with-it/`, including idempotence and silent skip outside git worktrees.
  - Delegated review lifecycle: default enabled review, review-band bump, degraded same-band fallback, two-cycle cap, verdict routing, modification-agent spawning for eligible `revise`, and no modification agent for `reject`.
  - Quality and closure loop: review individual diffs and combined batch diffs, run issue-specific checks before broad suites, commit per issue by default, update terminal issues, and close completed issues unless explicitly left open.
  - Terminal issue comment template and the rule that final issue comments are posted only for terminal outcomes: `completed`, `blocked`, or `failed-review`.
- passages to tighten:
  - Reorganize the file into a short core workflow plus compact inline appendices. Suggested core sections: `Purpose`, `When To Use`, `Inputs`, `Hard Boundaries`, `Workflow`, `Outputs`, and `Handoff`. Suggested inline appendices: `Routing Contract`, `Status and Ledger Contract`, `Review Orchestration Contract`, `Resume and State Contract`, and `Terminal Issue Comment Contract`.
  - Treat split candidates as internal structure unless reliable loading is implemented. The routing contract, status/ledger contract, review orchestration contract, resume/state contract, and terminal issue comment contract are too important to depend on external sub-docs without a guaranteed loader. If future skill-loading support can always include referenced files, these can become external documents; otherwise keep them as compact inline appendices.
  - Fix the asset completeness contract. The required-file list currently names `run-agent.sh` but not `run-agent.ps1`, while the Windows execution path requires `run-agent.ps1`. Prefer a single complete asset root containing both runners so copy-fix messages and preflight stay cross-platform; if the rewrite allows OS-minimal roots instead, that exception must be explicit and shared by asset discovery, copy-fix messages, and preflight.
  - Tighten the goal and execution wording so it says "execute the OS-selected unified runner" rather than only `run-agent.sh`.
  - Align runner invocation wording. The router says always pass both `AGENT` and `MODEL`; the execution section still describes selected `MODEL` as optional/defaulted from the registry. For coordinator-owned routed runs, `MODEL` should be explicit. Runner defaults may remain a runner fallback, not the coordinator contract.
  - Clarify no-git and fresh-project behavior as a first-class runtime path: filesystem asset discovery, local issue intake, empty commit context when git metadata is absent, no `.gitignore` mutation outside git, and clear terminal behavior when issue updates cannot be posted.
  - Tighten `Issue Intake` to define local `issues.md` parsing expectations, how `LOCAL_ISSUES_FILE` is located, and how provided issue context outranks fetched or local context.
  - Rename or qualify "Deterministic Router." Scoring and eligibility are deterministic, but the model pool and interchangeable GPT agent selection include random choice. The rewrite should target "deterministic scoring with auditable selection" and record selection reasoning in the existing route, status, and ledger outputs rather than adding a separate audit-log mechanism.
  - Keep `agent-registry.json` as the data source for score-to-weight ranges, model catalog, provider rules, required band models, interchangeable agent groups, fallback order, detection commands, and known model lists. The skill can summarize the rules, but duplicated registry tables should be compact and explicitly described as derived from the registry.
  - Tighten model-first selection so forced overrides, user "best possible model" instructions, hard minimum overrides, required band models, allowlist/denylist, provider exclusions, and fallback diagnostics have a single ordered decision tree.
  - Clarify provider fallback phases. Non-Google candidates should exhaust bounded automatic fallback before Google/Gemini last-resort evaluation, and last-resort attempts should be reported separately from `MAX_AGENT_FALLBACKS`.
  - Make the runner invocation contract platform-neutral and explicit: environment variables, context file, prompt file, selected agent, selected model, unattended mode, GUI mode behavior, and dry-run/listing behavior if used by preflight.
  - Tighten status messages into a grammar contract. Keep each parseable line stable, define required fields versus optional fields, preserve field order where backward compatibility is promised, and state when each line is emitted.
  - Tighten ledger and token telemetry language. `run-agent.sh` and `run-agent.ps1` currently emit runner-default telemetry with unknown token counts. The coordinator should normalize provider-native telemetry when available, preserve unknowns when not available, distinguish child-agent telemetry from coordinator estimates, and keep final summary sections aligned with parseable ledger rows.
  - Clarify context-budget accounting so direct file reads, issue payloads, diffs, archived review JSON re-reads, emitted status lines, and ledger rows have one counting rule. The estimator is approximate; the rewrite should say it is a halt threshold, not precise billing telemetry.
  - Tighten `host_context_window` lookup. The active host model may not be the same as the selected child model. If the host model cannot be detected, the existing `200000` fallback should remain explicit.
  - Make resume/discard behavior operationally exact: prompt before fresh issue intake, delete only `.run-with-it/state.json` on discard, preserve archived reviews, do not re-emit restored ledger lines during rehydration, and merge restored rows into the final ledger.
  - Tighten `.run-with-it/state.json` schema wording so array versus object shape is unambiguous, especially `review_history`, and so added fields are allowed without omitting the four required categories.
  - Tighten in-flight resume rules around duplicated work. Reattempted agents are fresh attempts; the coordinator should record that prior partial output is not accepted unless separately captured and reviewed.
  - Tighten `.gitignore` auto-append as a coordinator side effect owned only by `.run-with-it/` persistence. It should preserve contents, append only once, and not create `.gitignore` in no-git folders.
  - Replace temporal wording such as "today's inline-review behavior" with stable behavior names. The audit should not leave time-relative behavior undefined.
  - Clarify delegated review disabled mode. When `DELEGATED_REVIEW=false`, no review or modification child agents run, no review status lines are emitted, and no review/modify ledger rows are written; the coordinator still owns inline review quality before integration.
  - Clarify reviewer-band reachability. Preflight should report degraded mode once per run when higher-band review is unreachable, while per-task `review-degraded` should remain once per affected task if the degraded path activates.
  - Tighten verdict routing. `approve` integrates after verification and commit policy; `revise` can spawn a modification agent only before cap exhaustion; `reject` and cap exhaustion terminate as `failed-review`; all terminal paths should archive available reviewer JSON before status/comment updates.
  - Tighten modification-agent behavior. It should receive the original issue context, prompt, reviewed diff, and complete archived reviewer JSON, and it should stay inside the original issue scope and ownership boundaries.
  - Clarify terminal issue updates. The terminal comment template is coordinator-owned, task-specific token usage excludes coordinator/run totals, review summary line appears only when delegated review is enabled, and blocked or failed-review issues should be updated but not closed as completed. Cross-reference the status/ledger token rule so terminal comments use task-specific child-agent telemetry only, while coordinator and run aggregates appear only in final run summaries.
  - Tighten commit/integration policy. Commit per issue remains the default after accepted diffs and verification, but the rewrite should say how to handle dirty worktrees, blocked tasks, failed-review tasks, user-provided no-commit instructions, and multi-agent batch integration.
- passages to move, with destination:
  - Move the authoritative reviewer JSON field schema, verdict vocabulary meaning, comment object shape, and `blocking_reasons` rule to `assets/review-prompt.md`. In `run-with-it`, keep a compact parse/validation summary that says the coordinator consumes the JSON artifact defined by `assets/review-prompt.md`.
  - Move any implementation-agent testing methodology, red/green/refactor detail, happy/negative path detail, or public-interface testing explanation to `skills/tdd-implementation/SKILL.md`. `run-with-it` should only invoke or pass through TDD requirements when the issue calls for them.
  - Move any implementation-agent scope discipline, "read nearby code before editing" guidance, and implementation completion report shape to `assets/prompt.md`. `run-with-it` should own assignment, payload assembly, and interpretation of results, not the implementer's coding process.
  - Move any PRD synthesis, initial issue body template, label vocabulary, dependency-order issue creation, local `prd.md`/`issues.md` authoring, or advisory routing-hint definition to `skills/create-git-issue/SKILL.md`.
  - Move exact runner CLI execution mechanics, GUI permission downgrades, command construction, command detection, and telemetry emission implementation details to `run-agent.sh` and `run-agent.ps1` comments/help if they need documentation. `run-with-it` should keep only the invocation and preflight contract it must obey.
  - Move any registry data duplication that is not obedience-critical back to `assets/agent-registry.json` as data. `run-with-it` may keep short examples of score bands and provider rules, but should not become a second registry.
- passages to remove:
  - Remove the duplicated full reviewer JSON schema block from `run-with-it` unless the rewrite explicitly justifies a very small inline mirror as obedience-critical for coordinator parsing. The source of truth for artifact shape should be `assets/review-prompt.md`.
  - Remove or rewrite "Reviewer JSON contract from the PRD"; the PRD is not the runtime owner of reviewer output. The reviewer prompt owns the artifact shape, and `run-with-it` owns lifecycle and routing.
  - Remove stale or conflicting statements that imply only `run-agent.sh` is used. Windows execution requires `run-agent.ps1`.
  - Remove the implication that selected `MODEL` is optional in normal coordinator-routed execution. The unified runner may default internally, but `run-with-it` should pass the selected model explicitly.
  - Remove time-relative wording such as "today's inline-review behavior" because it is not a stable contract.
  - Remove any repeated full registry tables that drift from `agent-registry.json` if a compact summary plus registry reference is enough for obedience.
  - Remove any future wording that lets `run-with-it` create PRDs, author initial implementation issue templates, change labels beyond terminal execution updates, rewrite `prompt.md` or `review-prompt.md`, modify runner scripts, or change model registry data during execution.
- duplicated contracts and source-of-truth handling:
  - Final runtime routing authority: `skills/run-with-it/SKILL.md` is authoritative. `skills/create-git-issue/SKILL.md` may produce advisory routing hints only, and generated issues must say those hints do not bind runtime routing.
  - Registry-backed routing data: `assets/agent-registry.json` is the data source for model catalog, score-to-weight ranges, provider rules, required band models, fallback order, interchangeable agent groups, detection metadata, and known models. `run-with-it` owns the runtime algorithm that consumes that data.
  - Runner execution: `run-agent.sh` and `run-agent.ps1` execute selected parameters, validate runner-level inputs, build payloads, apply GUI-safe permission adjustments, and emit runner-default telemetry. They do not own issue selection, scoring, model choice, provider fallback, queue planning, or review lifecycle.
  - Implementation-agent behavior: `assets/prompt.md` owns implementation guardrails for an already assigned scope. `run-with-it` owns assignment, ownership boundaries, payload assembly, verification expectations, integration decisions, and interpretation of the implementer's completion report.
  - TDD methodology: `skills/tdd-implementation/SKILL.md` owns red/green/refactor, behavior-first public-interface tests, and positive/negative path methodology. `run-with-it` may require the implementation prompt to invoke that skill when the issue asks for test-first work.
  - Reviewer behavior and JSON artifact: `assets/review-prompt.md` owns reviewer read-only restrictions and the authoritative JSON shape. `run-with-it` owns when reviewers run, reviewer/model selection, payload assembly, archival under `.run-with-it/reviews/`, verdict routing, modification-agent spawning, and terminal issue updates.
  - Status lines, route output, ledger rows, token summaries, and terminal issue comments: `skills/run-with-it/SKILL.md` is authoritative. Prompts and generated issues should not duplicate these formats except for minimal references consumed by the coordinator.
  - Resume and persisted state: `skills/run-with-it/SKILL.md` is authoritative for `.run-with-it/state.json`, `.run-with-it/reviews/`, compaction handoff, resume/discard prompting, in-flight reattempts, restored ledger rows, and `.gitignore` auto-append behavior.
  - Terminal issue updates and commits: `skills/run-with-it/SKILL.md` owns terminal comment posting, completed issue closure, blocked/failed-review handling, and commit-per-issue default. `create-git-issue` owns initial issue publication, not terminal execution updates.
- authority changes, if any: None to the intended runtime boundary. The rewrite should preserve `run-with-it` as the final runtime routing and coordination authority. Recommended moves are source-of-truth clarifications: reviewer JSON artifact shape belongs to `assets/review-prompt.md`, implementation discipline belongs to `assets/prompt.md` and `skills/tdd-implementation/SKILL.md`, initial issue creation belongs to `skills/create-git-issue/SKILL.md`, runner mechanics belong to the runner scripts, and registry data belongs to `assets/agent-registry.json`.
- acceptance checks for the rewrite:
  - YAML front matter triggers on runtime execution of ready issues and does not imply PRD synthesis, initial issue creation, or production file rewriting.
  - The opening workflow position still places `run-with-it` after `create-git-issue` and says advisory issue routing hints do not bind runtime routing.
  - The body states that final runtime routing authority remains with `run-with-it`.
  - The body keeps OS detection, asset discovery, no-git support, local issue fallback, and platform-specific runner selection coherent with each other.
  - Asset discovery and preflight agree on whether `run-agent.ps1` is required always or only on Windows.
  - Routed execution always passes selected `AGENT` and `MODEL` explicitly to the OS-selected unified runner.
  - Complexity scoring, hard minimum overrides, model-first selection, provider rules, allowlist/denylist behavior, forced overrides, and bounded fallback form one ordered decision tree.
  - Registry data is referenced as data from `assets/agent-registry.json`; the skill does not become a second registry.
  - Multi-agent coordination rules preserve one coordinator, explicit ownership scopes, safe parallelism, sequential fallback for dependency-sensitive work, per-agent result review, and closed child-agent lifecycle.
  - Reviewer lifecycle remains coordinator-owned: review-band bump, degraded fallback, two-cycle cap, archived JSON, verdict routing, modification-agent payload, and failed-review terminal behavior.
  - Reviewer JSON artifact shape is owned by `assets/review-prompt.md`; any schema summary in `run-with-it` is explicitly non-authoritative and compact.
  - Status messages, parseable route output, ledger rows, token telemetry fields, final token summaries, and terminal issue comment template remain owned by `run-with-it`.
  - Token telemetry supports provider-native values when available, runner-default `unknown` values when not available, coordinator-estimated values when applicable, and role/run aggregates in the required summary sections.
  - Context-budget tracking, compaction handoff, `.run-with-it/state.json`, resume/discard prompting, restored ledger handling, in-flight reattempts, and `.gitignore` auto-append remain self-contained and source-of-truth in `run-with-it`.
  - Terminal issue comments are posted only for `completed`, `blocked`, and `failed-review`, use task-specific token usage, and distinguish issue update behavior from initial issue creation.
  - Commit/integration policy is explicit for completed, blocked, failed-review, dirty-worktree, no-git, no-gh, and user-requested no-commit cases.
  - Split candidates are handled under the reliable-loading rule: no external sub-doc dependency unless loading/inclusion is guaranteed; otherwise use compact inline appendices.
  - No production skill, prompt, runner, registry, or test file rewrite is included in this audit output.

## Consolidated Final Rewrite Plan

| Scoped file | Target role | Authority boundary |
| --- | --- | --- |
| `skills/break-req/SKILL.md` | Requirements discovery and decision-tree resolution before implementation planning. | May create or update only `technical_requirements.md`; must not implement, create issues, run downstream skills, invoke external coding agents, or proceed past requirements handoff. |
| `skills/create-git-issue/SKILL.md` | Convert resolved requirements into a PRD and dependency-aware implementation issue slices. | Owns PRD synthesis, issue templates, labeling guidance, local `prd.md`/`issues.md` fallback, dependency ordering, and advisory routing hints; must not assign concrete agents/models or execute work. |
| `skills/run-with-it/SKILL.md` | Runtime execution coordinator for ready issues. | Owns issue intake for execution, final agent/model routing, queue/dependency decisions, safe multi-agent coordination, runner invocation contract, delegated review lifecycle, persisted run state and resume behavior, status/ledger/token output, terminal issue comments, issue closure, and per-issue commits. |
| `skills/save-tokens/SKILL.md` | Response compression mode. | Owns wording style for compressed assistant responses only; must not change planning, routing, review, implementation, code blocks, commit messages, PR descriptions, formal plans, issue bodies, review artifacts, prompts, command output requiring fidelity, or persisted artifacts. |
| `skills/tdd-implementation/SKILL.md` | Test-first implementation discipline for already assigned work. | Owns red/green/refactor workflow, behavior-first public-interface testing rules, positive and negative path coverage, green-only refactoring, and per-cycle checks; must not select issues, route agents/models, manage queues, coordinate execution, update GitHub, create commits, or own repo-specific runtime state. |
| `assets/prompt.md` | Implementation-agent prompt for an already assigned issue and scope. | Owns implementation guardrails, local exploration, verification expectations, implementer self-checks, and completion reporting; must not perform issue selection, dependency planning, runtime routing, orchestration, persisted status/ledger behavior, issue updates, delegated review lifecycle, or reviewer JSON output. |
| `assets/review-prompt.md` | Reviewer-agent prompt. | Owns read-only reviewer behavior and the authoritative reviewer JSON artifact shape; must not edit the working tree, run git or `gh`, update issues, create commits, coordinate review lifecycle, manage archive paths, or emit narrative output after review completion. |

This table is the complete rewrite scope. Later references to runner scripts, registry JSON, tests, README, generated issues, or local artifacts are boundary notes only and do not add those files to the rewrite scope.

### Final Verdicts

| Scoped file | Primary verdict | Rewrite result |
| --- | --- | --- |
| `skills/break-req/SKILL.md` | `tighten` | Keep the requirements-only workflow, make triggers and handoff language narrower, and preserve `technical_requirements.md` as the only writable artifact. |
| `skills/create-git-issue/SKILL.md` | `tighten` | Keep PRD and initial issue creation authority, make GitHub/local fallback, approval gates, labels, dependencies, and advisory routing hints explicit, and prevent execution-time authority. |
| `skills/run-with-it/SKILL.md` | `split` | Keep runtime coordination authority, reorganize the large file into a compact core plus reliable inline appendices, and remove or demote duplicated contracts owned elsewhere. |
| `skills/save-tokens/SKILL.md` | `tighten` | Keep compressed response style, narrow triggers to response-mode intent, and explicitly protect durable artifacts and exact technical content from transformation. |
| `skills/tdd-implementation/SKILL.md` | `tighten` | Keep the TDD methodology, scope it to assigned implementation work, and remove ambiguity around planning, routing, review, GitHub, commit, or runtime-state authority. |
| `assets/prompt.md` | `tighten` | Keep implementation guardrails for assigned work, add expected inputs, reference TDD as the methodology owner, and keep coordinator and reviewer contracts out. |
| `assets/review-prompt.md` | `tighten` | Keep read-only review behavior and the JSON artifact contract, require a coordinator-supplied output path, and keep lifecycle and archive behavior in the coordinator. |

### Rewrite Order

1. Rewrite `skills/break-req/SKILL.md` first so the upstream requirements boundary is crisp before downstream planning text is rewritten.
2. Rewrite `skills/create-git-issue/SKILL.md` next so PRD synthesis, initial issue templates, labels, local fallback, dependency ordering, and advisory routing hints are settled before runtime coordination is rewritten.
3. Rewrite `skills/tdd-implementation/SKILL.md` and `assets/prompt.md` together as one implementation-discipline pass: the TDD skill owns methodology, and the implementation prompt owns assigned-scope guardrails plus completion reporting.
4. Rewrite `assets/review-prompt.md` before the final `run-with-it` pass so the reviewer JSON artifact shape and read-only reviewer behavior are authoritative before the coordinator references them.
5. Rewrite `skills/save-tokens/SKILL.md` as a small independent pass, preserving response style only and denying durable artifact transformation.
6. Rewrite `skills/run-with-it/SKILL.md` last because it consumes the settled upstream, implementation, review, runner, registry, status, resume, and terminal-update boundaries.

### Authority Changes

Rows naming out-of-scope owners are boundary assignments only; they prevent accidental rewrites and do not expand the seven-file rewrite scope.

| Contract or duplicated authority | Old owner | Proposed new owner | Reason | Expected behavior |
| --- | --- | --- | --- | --- |
| Requirements discovery and handoff | Ambiguous pressure between `skills/break-req/SKILL.md` and downstream planning skills. | `skills/break-req/SKILL.md` | Requirements must be resolved before PRD or runtime work, and the skill already has the only-write-`technical_requirements.md` hard stop. | `break-req` produces or updates `technical_requirements.md` and stops; downstream skills consume resolved requirements and ask only focused follow-ups for their own phase. |
| PRD synthesis, initial issue templates, labels, local fallback, dependencies, and advisory routing hints | Ambiguous overlap between `skills/create-git-issue/SKILL.md` and runtime coordinator wording. | `skills/create-git-issue/SKILL.md` | Initial issue authoring is a planning/publishing phase, not runtime execution. | `create-git-issue` creates or locally writes approved PRD and implementation issue bodies; `run-with-it` consumes ready issues but does not author PRDs or initial templates. |
| Final issue intake, queue/dependency decisions, final agent/model routing, and safe multi-agent coordination | Advisory routing hints in `skills/create-git-issue/SKILL.md` plus runtime rules in `skills/run-with-it/SKILL.md`. | `skills/run-with-it/SKILL.md` | Routing hints are static planning metadata; runtime routing must evaluate current issue context, constraints, registry data, and available agents. | Generated issue routing hints remain advisory and never name binding concrete agents/models; `run-with-it` makes final runtime routing and queue decisions. |
| Implementation-agent scope discipline and completion report shape | Duplicated or at risk of duplication between `assets/prompt.md` and coordinator text. | `assets/prompt.md` | The implementation prompt is the assigned worker's local behavioral contract; the coordinator should assemble and interpret payloads, not restate coding workflow. | `assets/prompt.md` defines assigned-scope implementation guardrails and completion fields; `run-with-it` supplies context, evaluates results, and owns status/ledger/issue updates. |
| Test-first methodology | Duplicated between `skills/tdd-implementation/SKILL.md`, `assets/prompt.md`, issue templates, and potential coordinator wording. | `skills/tdd-implementation/SKILL.md` | Red/green/refactor, public-interface testing, and positive/negative path coverage need one methodology source of truth. | Implementation prompt and issue templates may invoke or reinforce TDD briefly; detailed workflow stays in the TDD skill. |
| Reviewer JSON artifact shape and reviewer verdict vocabulary | Duplicated between `assets/review-prompt.md` and `skills/run-with-it/SKILL.md`. | `assets/review-prompt.md` | The reviewer prompt is the only component that writes the artifact and must own exact fields, verdict values, comment shape, and blocking-reason rules. | `assets/review-prompt.md` defines the authoritative JSON; `run-with-it` keeps only compact parse/validation expectations and routes parsed verdicts. |
| Delegated review lifecycle, archive handling, verdict routing, modification agents, and terminal review outcomes | Ambiguous overlap between `assets/review-prompt.md` and `skills/run-with-it/SKILL.md`. | `skills/run-with-it/SKILL.md` | Lifecycle and issue updates are coordinator responsibilities; reviewer agents must stay read-only and artifact-only. | `review-prompt` produces exactly one JSON artifact and stops; `run-with-it` schedules review, supplies output paths, archives artifacts, routes verdicts, spawns modification agents when allowed, and updates terminal issues. |
| Status lines, route output, ledgers, token summaries, persisted state, resume behavior, terminal issue comments, issue closure, and per-issue commits | At risk of drifting into implementation prompts, review prompts, or generated issue templates. | `skills/run-with-it/SKILL.md` | These are execution-time coordinator contracts and must remain parseable and phase-specific. | Prompts and generated issues do not duplicate coordinator formats; `run-with-it` owns runtime status, ledger, token, state, terminal comment, closure, and commit behavior. |
| Response compression behavior | Broad trigger wording in `skills/save-tokens/SKILL.md` could be read as permission to compress durable artifacts. | `skills/save-tokens/SKILL.md` for assistant response wording only. | Token reduction must not weaken formal contracts or mutate content intended to be saved, copied, parsed, or executed. | Save-token mode affects only assistant narration; code blocks, formal plans, issue bodies, review JSON, prompts, commit messages, PR descriptions, command output needing fidelity, and persisted artifacts remain exact. |
| Runner execution mechanics | Coordinator wording may over-describe command construction or runner internals. | `run-agent.sh` and `run-agent.ps1` as external non-rewrite owners. | Runtime coordination chooses the selected agent/model and invokes the OS-selected runner, but the runner scripts own execution mechanics. | `run-with-it` keeps a platform-neutral invocation and preflight contract, passes explicit `AGENT` and `MODEL`, and does not rewrite runner mechanics in this plan. |
| Registry data and model catalog facts | Coordinator wording may duplicate registry tables or provider/model data. | `assets/agent-registry.json` as external non-rewrite owner. | Registry data must not drift between the coordinator and the data file. | `run-with-it` owns the algorithm consuming registry data, but model catalog, score bands, provider rules, fallback order, detection metadata, and known models stay registry-owned. |

### Duplicated Contract Ownership

| Contract | Authoritative owner | Allowed local reinforcement |
| --- | --- | --- |
| Requirements-only discovery and handoff | `skills/break-req/SKILL.md` | A downstream skill may say it consumes resolved requirements; it must not re-run the requirements phase. |
| PRD and initial implementation issue body templates | `skills/create-git-issue/SKILL.md` | Runtime execution may consume issue bodies but must not redefine their template or label policy. |
| Advisory routing hints | `skills/create-git-issue/SKILL.md` | `run-with-it` may read hints as inputs while stating they are non-binding. |
| Final runtime routing and queue decisions | `skills/run-with-it/SKILL.md` | Issue bodies may carry advisory metadata; prompts must not choose new work. |
| Implementation TDD methodology | `skills/tdd-implementation/SKILL.md` | `assets/prompt.md` may invoke the skill and keep one compact positive/negative-path reminder. |
| Implementation assigned-scope guardrails | `assets/prompt.md` | `run-with-it` may summarize ownership boundaries in the payload it sends to an implementer. |
| Reviewer JSON artifact shape | `assets/review-prompt.md` | `run-with-it` may keep a compact non-authoritative parse summary and must reference the review prompt as source of truth. |
| Reviewer lifecycle and terminal review handling | `skills/run-with-it/SKILL.md` | `assets/review-prompt.md` may say the coordinator provides the output path and consumes the artifact. |
| Status, ledger, token, persisted-state, terminal-comment, closure, and commit contracts | `skills/run-with-it/SKILL.md` | Child prompts and generated issues may mention that the coordinator owns these outputs; they should not duplicate parseable formats. |
| Response compression mode | `skills/save-tokens/SKILL.md` | Other files may rely on their formal output contracts remaining exact under compressed assistant narration. |

### File Rewrite Requirements

- `skills/break-req/SKILL.md`: Use the light skill structure. Keep "requirements-only" and the single writable output before workflow steps. Tighten front matter around requirements discovery, dependency mapping, and technical constraint capture. Replace hostile or subjective phrasing with a completeness standard. Preserve the stop-and-handoff line to the user without invoking downstream skills.
- `skills/create-git-issue/SKILL.md`: Use the light skill structure. Keep approval gates for the PRD and issue breakdown, exact local fallback files, exact implementation issue section order, canonical label roles, body-file publishing with `gh`, and advisory routing language. Clarify partial publish failure and fallback collision behavior. Do not copy the runtime router.
- `skills/run-with-it/SKILL.md`: Use a short core workflow plus compact inline appendices unless reliable external loading is guaranteed. Required appendices are routing, status/ledger/token output, review orchestration, resume/state, and terminal issue comments. Keep final runtime routing authority, explicit `AGENT` plus `MODEL` runner invocation, safe multi-agent coordination, delegated review lifecycle, persisted state, terminal issue updates, issue closure, and per-issue commits. Demote reviewer schema, TDD methodology, implementation prompt, runner mechanics, and registry tables to references or compact summaries.
- `skills/save-tokens/SKILL.md`: Keep the file small. Scope triggers to explicit compressed-response intent. State that compression affects only assistant wording and never durable artifacts, quoted source, code blocks, technical terms, parseable output, command output requiring fidelity, prompts, issue bodies, review JSON, commit messages, PR descriptions, or persisted files.
- `skills/tdd-implementation/SKILL.md`: Use the light skill structure while preserving strict red/green/refactor, vertical tracer bullets, public-interface testing, positive and negative path coverage, green-only refactoring, tech-stack alignment, and the per-cycle checklist. Rewrite "Plan with user" as blocker-only calibration for already assigned work.
- `assets/prompt.md`: Use the prompt-specific structure. Add expected inputs for already assigned issue context, scope limits, constraints, and coordinator-provided work. Keep exploration-before-code, minimal-scope implementation, verification, self-check, completion report, and conditional no-more-tasks sentinel. Reference TDD as the methodology owner instead of copying a full workflow.
- `assets/review-prompt.md`: Use the prompt-specific structure. Add expected inputs including coordinator-provided task context, diff, changed-file summary when available, verification evidence when present, and mandatory output path. Keep read-only restrictions, forbid git/gh/issues/commits/branches/tags, require exactly one JSON artifact, and define `verdict`, `summary`, `comments`, and `blocking_reasons` as the authoritative reviewer output.

### Rewrite Acceptance Checks

- The rewrite pass changes only the seven scoped files named in the responsibility map; it does not rewrite runners, registry data, tests, generated issues, README, or other docs unless a later issue explicitly expands scope.
- Each rewritten file states its phase boundary before detailed workflow steps.
- Each rewritten skill uses the light structure or justifies a compact variant; each rewritten prompt uses or maps clearly to the prompt-specific structure.
- Every duplicated contract names one authoritative owner and either uses a compact local reference, intentional reinforcement, or removes the duplicate.
- Authority moves preserve behavior and improve obedience; token reduction is allowed only after hard stops, source-of-truth ownership, output contracts, and safety constraints remain enforceable.
- `break-req` can only write `technical_requirements.md` and stops before PRD, issue creation, routing, implementation, review, external agents, or GitHub publishing.
- `create-git-issue` can synthesize approved PRDs and initial issue slices, publish with `gh`, or write `prd.md` and `issues.md`; it cannot execute work, choose concrete agents/models, coordinate runtime agents, perform delegated review, close issues, or make terminal updates.
- `run-with-it` consumes ready issues or local issue files, makes final runtime routing decisions, invokes the selected runner with explicit selected agent/model, coordinates safe parallel work, owns status/ledger/token/state/review lifecycle, posts terminal issue comments, closes completed issues when allowed, and commits per issue by default.
- `save-tokens` affects only assistant response style and cannot transform durable artifacts or exact content.
- `tdd-implementation` governs testing methodology for assigned work and cannot select issues, route agents/models, update GitHub, create commits, coordinate queues, or own runtime state.
- `assets/prompt.md` governs implementation-agent behavior for assigned scope and cannot define reviewer JSON, issue selection, queue planning, final routing, terminal issue comments, ledgers, or persisted state.
- `assets/review-prompt.md` governs read-only review and the reviewer JSON artifact; it cannot run git or `gh`, edit files, update issues, create commits, coordinate lifecycle, or print narrative output after completion.
- The final rewritten Markdown remains sufficient for an implementation pass to proceed without re-interviewing the user about ownership, verdicts, rewrite order, or cross-file contract boundaries.

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
