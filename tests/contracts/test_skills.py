"""Skill authority boundaries and maintained Markdown/runtime contracts.

These assertions target schemas, sequencing and authority invariants. Executable
runtime behavior is covered by the native unit/integration suites, rather than
by looking for implementation text in a former shell script.
"""
from pathlib import Path
import json
import re

import pytest

from ai_skills import artifacts, pair_documents, routing
from ai_skills.pair import PairConfig
from ai_skills.resources import resource_path

ROOT = Path(__file__).resolve().parents[2]


def skill(name):
    return (ROOT / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')


def resource(name):
    return resource_path(name).read_text(encoding='utf-8')


def section(text, title):
    match = re.search(r'^## ' + re.escape(title) + r'\s*\n(.*?)(?=^## |\Z)',text,re.M|re.S)
    assert match, f'missing section: {title}'
    return match[1]


def fences(text, language):
    return re.findall(r'^```'+re.escape(language)+r'\s*\n(.*?)^```\s*$',text,re.M|re.S)


def json_examples(text):
    return [json.loads(body) for body in fences(text,'json')]


@pytest.mark.parametrize('name,mode,outputs',[
    ('break-req','requirements-only',{'technical_requirements.md'}),
    ('help-me-debug','diagnosis-only',{'debug_human_report.md','debug_llm_context.md'}),
])
def test_discovery_skills_limit_authority_and_output_files(name,mode,outputs):
    text=skill(name)
    assert mode in section(text,'Purpose')
    boundary=section(text,'Hard Stop')
    assert 'Never implement code' in boundary
    for forbidden in ('edit product files','create issues','publish to GitHub','run `create-git-issue`','run `run-with-it`','spawn implementation agents','invoke external coding agents'):
        assert forbidden in boundary
    allowed_line=next(line for line in boundary.splitlines() if line.startswith('The only file'))
    assert set(re.findall(r'`([^`]+\.md)`',allowed_line))==outputs
    assert 'read-only exploration' in boundary.lower()


def test_requirements_workflow_stops_before_issue_creation():
    text=skill('break-req')
    workflow=section(text,'Workflow')
    assert 'one at a time' in workflow
    assert 'explore the codebase instead of asking' in workflow
    output=section(text,'Outputs')
    assert output.index('`technical_requirements.md`') < output.index('Stop.') < output.index('`create-git-issue`')
    assert 'Do not proceed beyond `technical_requirements.md`' in output
    assert '**Implementation Steps**' in output and '[HITL]' in output


def test_diagnosis_investigation_and_completion_gates_are_ordered():
    workflow=section(skill('help-me-debug'),'Workflow')
    ordered=['Frame the problem','project orientation snapshot','deep codebase investigation','diagnosis decision tree','exactly one targeted question','Resolve diagnosis branches','confidence level','completion gate']
    offsets=[workflow.index(value) for value in ordered]
    assert offsets==sorted(offsets)
    branches=re.findall(r'\*\*([^*]+ branch)\*\*',workflow)
    assert branches==['Reproducibility branch','Scope branch','Change-surface branch','Failure-mode branch','Confidence branch']
    for dependency in ('Map internal dependencies','Map external dependencies','record evidence','explain the failure or bound the fix'):
        assert dependency in workflow


def test_diagnosis_waits_for_human_answer_before_final_artifacts():
    text=skill('help-me-debug')
    workflow=section(text,'Workflow')
    assert "pause and wait for the user's answer" in workflow
    assert 'Do not continue investigation finalization or report generation while the question is pending' in workflow
    assert 'must ask at least one targeted question before finalizing' in workflow
    assert 'human-answerable' in workflow and 'not-currently-answerable' in workflow
    output=section(text,'Outputs')
    assert 'Awaiting user answer: <question>' in output
    assert 'do not proceed beyond report generation' in output.lower()


def test_diagnosis_handoff_schema_stays_standalone_and_ordered():
    text=skill('help-me-debug')
    handoff=section(text,'Deterministic Handoff Rules')
    for field in ('exact file paths','symbols','expected behavior','preconditions','postconditions','validation commands','without reading the chat history'):
        assert field in handoff
    output=section(text,'Outputs')
    headings=re.findall(r'^\s*- `([^`]+)`\s*$',output,re.M)
    assert headings==['Project Overview','Architecture Map','Internal Dependencies','External Dependencies','Critical Call Paths','Fault Surface Inventory','Implementation Approach','File-by-File Change Guide','What NOT to change','Constraints','Dependencies & Libraries','Test files to update','Acceptance Criteria']
    assert '`No tests present.`' in output
    assert 'Expected vs Actual:' in output and 'Architecture and Dependency Summary:' in output
    assert 'Call Path Trace:' in output and 'one step per line using `->`' in output


def test_issue_template_headings_match_the_declared_publishing_order():
    text=skill('create-git-issue')
    expected=['Parent','What to build','Implementation Steps','Agent Routing','Technical Context Snapshot','Acceptance criteria','Blocked by']
    declared=re.findall(r'^\d+\. `## ([^`]+)`$',text,re.M)
    template=re.search(r'<issue-template>(.*?)</issue-template>',text,re.S)
    assert template
    actual=re.findall(r'^## (.+)$',template[1],re.M)
    assert declared==actual==expected


def test_issue_routing_hints_use_the_runtime_band_vocabulary_without_binding_models():
    text=skill('create-git-issue')
    examples=[body for body in fences(text,'yaml') if body.startswith('agent_routing:')]
    assert len(examples)==1
    block=examples[0]
    fields=dict(re.findall(r'^  ([a-z_]+):\s*(.*)$',block,re.M))
    assert set(fields)=={'complexity_hint','required_capability','parallel_safe','cost_preference','speed_preference','ownership_scope','verification'}
    assert fields['complexity_hint'].strip('<>').split('|')==routing.BAND_ORDER
    assert fields['required_capability']=='<fast|balanced|advanced>'
    assert fields['parallel_safe']=='<true|false>'
    assert 'must not assign concrete agent/model names' in text
    assert 'run-with-it remains the final runtime routing authority' in text


def test_issue_publishing_fallback_preserves_the_same_issue_contract():
    text=skill('create-git-issue')
    assert 'append each approved slice issue to `issues.md`' in text
    assert 'keep each implementation issue nearly identical to the GitHub issue body format' in text
    assert 'Do not publish implementation slice issues until this review step is approved' in text
    assert 'Publish or write local issues in dependency order' in text
    assert 'If using local fallback, reference `prd.md` as the parent' in text


@pytest.mark.parametrize('name',['prompt.md','modifier-prompt.md'])
def test_verified_noop_examples_are_accepted_by_the_real_artifact_validator(name):
    text=resource(name)
    examples=[value for value in json_examples(text) if value.get('no_op') is True]
    assert examples, f'{name} must include a complete verified no-op example'
    for payload in examples:
        decision=artifacts.validate_artifact(payload['role'],payload,artifacts.GitEvidence(issue=str(payload['issue'])))
        assert decision.valid,decision.reason
        invalid={**payload,'verification':{'passed':False}}
        assert artifacts.validate_artifact(payload['role'],invalid,artifacts.GitEvidence(issue=str(payload['issue']))).reason=='verified-no-op-requires-passing-verification'
    assert text.index('## Result Artifact') < text.index('## Completion Sentinel')
    assert 'ai-skills artifact write-json' in text


def test_complexity_example_is_valid_and_enforces_grounding_and_peak_floor():
    prompt = resource('complexity-prompt.md')
    examples = [value for value in json_examples(prompt) if 'scores' in value and 'rationale' in value]
    assert examples and all(artifacts.valid_complexity_payload(value, allow_legacy=False) for value in examples)
    for value in examples:
        assert value['schema_version'] == artifacts.COMPLEXITY_SCHEMA_VERSION
        assert value['files_inspected']
    assert 'Peak-dimension floor' in prompt
    assert 'must not print those paths' not in prompt

    coordinator = resource('sub-coordinator-prompt.md')
    run_skill = skill('run-with-it')
    assert 'easy-medium' not in coordinator
    assert '8–12' not in coordinator and '33–40' not in coordinator and '9–12 is `quite-easy`' not in coordinator
    assert '7–9 is `quite-easy`' in coordinator and '29–35 `extreme`' in coordinator
    assert 'score=21' in coordinator and 'score=21' in resource('coordinator-rules.md')
    assert 'Do **not** include the blind complexity score' in coordinator
    assert '`RUN_WITH_IT_PLAN_MIN_COMPLEXITY` (default `medium`)' in coordinator
    assert '`RUN_WITH_IT_PLAN_MIN_COMPLEXITY` | `medium`' in run_skill
    assert '(default `medium`)' in run_skill
    native_block = coordinator.split('Native invocation (use only the current tool\'s documented permission mechanism if required):', 1)[1].split('Sub-agent output handling:', 1)[0]
    assert native_block.count('Write a fresh attempt-specific `worker.json`') == 1


def test_plan_example_is_a_valid_nonempty_plan_and_remains_read_only():
    text=resource('plan-prompt.md')
    plans=[value for value in json_examples(text) if value.get('role')=='plan']
    assert plans and all(artifacts.valid_plan_payload(value) for value in plans)
    for value in plans:
        assert value['slices'] and value['complexity_level'] in routing.BAND_ORDER
        assert value['complexity_rationale'].strip()
    assert 'band anchors' in text
    assert 'blind score' not in text.lower()
    assert 'Do NOT run tests' in text
    assert 'Do NOT edit, stage, or commit any code' in text
    assert 'plan.json' in text and 'plan.md' in text
    assert 'dispatcher validates both before publishing managed DONE' in text


def test_review_schema_vocabulary_matches_runtime_and_includes_plan_coverage():
    examples=json_examples(resource('review-prompt.md'))
    status=next(value for value in examples if set(value)=={'verdict','comment_count','nitpick_only'})
    instructions=next(value for value in examples if 'coverage_matrix' in value)
    assert set(status['verdict'].split(' | '))==artifacts.REVIEW_VERDICTS
    comment=instructions['comments'][0]
    assert set(comment['category'].split(' | '))==artifacts.REVIEW_CATEGORIES
    assert set(comment['severity'].split(' | '))==artifacts.REVIEW_SEVERITIES
    assert 'plan_conformance' in instructions['coverage_matrix'][0]['area'].split(' | ')
    assert {'id','file','line','severity','category','blocking','fix','evidence','expected_change','verification'} <= set(comment)


def test_orchestrator_issue_status_and_subcoordinator_outcome_names_stay_distinct():
    outcomes=next(value for value in json_examples(resource('sub-coordinator-prompt.md')) if 'outcome' in value)
    assert outcomes['outcome'].split(' | ')==['completed','failed-review','merge_failed','blocked']
    assert outcomes['review_skipped'] is False
    main=skill('run-with-it')
    schema=next(value for value in json_examples(main) if 'issue_registry' in value)
    statuses=next(iter(schema['issue_registry'].values()))['status'].split(' | ')
    assert set(statuses)=={'completed','in_progress','pending','merge_recovery','failed-review','failed-merge','blocked'}
    rules=resource('main-orchestrator-rules.md')
    terminal=re.search(r'every issue is terminal \(([^)]+)\)',rules)
    assert terminal
    assert {value.strip() for value in terminal[1].split('/')}=={'completed','failed-review','failed-merge','blocked'}
    assert 'pool-unreachable' in main and 'pool-unreachable' in rules
    assert 'failed-merge' not in resource('sub-coordinator-prompt.md')


@pytest.mark.parametrize('name',['sub-coordinator-prompt.md','coordinator-rules.md'])
def test_compaction_rules_keep_gate_attribution_and_merge_trigger(name):
    text=resource(name)
    assert 'Out-of-Scope Gate Rule' in text
    assert 'out-of-scope-gate-failure' in text
    assert 'STATUS|type=gate-attribution' in text
    assert 'fail closed' in text.lower()
    assert 'review skip' in text
    assert 'medium-hard' in text
    assert 'default to medium and continue' not in text
    assert 'ai-skills route' in text and 'ai-skills worker dispatch' in text


def test_recovery_rules_document_compaction_budget_and_liveness_backstops():
    coordinator=resource('sub-coordinator-prompt.md')
    assert '"compaction_requested": true' in coordinator
    assert 'MAX_SUB_COORD_COMPACTION_HANDOFFS' in coordinator
    assert '**Stop** and wait for the user.' not in coordinator
    orchestrator=skill('run-with-it')
    for key in ('MAX_WORKER_WAIT_SECONDS','RUN_WITH_IT_WORKER_STALE_SECONDS','MAX_SUB_COORD_COMPACTION_HANDOFFS'):
        assert key in orchestrator
    assert 'hardcoded to 8 cycles' in orchestrator
    assert 'Review: skipped' in orchestrator


def test_reviewer_band_increase_is_owned_by_router():
    text=resource('sub-coordinator-prompt.md')
    assert 'REVIEW_BUMP' in text and 'never pre-bump' in text
    assert 'prompt fallback router only' in text
    assert routing.routing_level('review','medium')=='medium-hard'


def test_modifier_verification_and_noop_baseline_are_scoped_to_review_cycle():
    text=resource('modifier-prompt.md')
    assert '--pre-spawn-head <saved-review-head-sha>' in text
    assert 'caused by the reviewed change before reporting completion' in text
    assert 'applies only to failures outside those required commands' in text
    assert 'verification.passed=true' in text
    assert 'If a required gate stays red, publish the shared protocol failed or blocked report' in resource('prompt.md')


@pytest.mark.parametrize('name',['save-tokens','tdd-implementation'])
def test_worker_skill_isolation_permits_governing_prompt_bootstrap(name):
    assert 'governing prompt' in section(skill(name),'Skill Isolation')


def test_pair_skill_defaults_match_the_native_configuration():
    text=skill('pair-colleague')
    config=PairConfig()
    for flag,default in [('min-rounds',config.min_rounds),('max-rounds',config.max_rounds),('timeout-seconds',config.timeout_seconds)]:
        assert re.search(r'--'+flag+r'` \(default '+str(default)+r'\b',text)
    assert 'never guess' in text and 'one question at a time' in text
    assert '| `4` |' in text
    assert 'AGENT_1_CMD' not in text
    assert config.purpose == 'general'
    assert '--purpose` (default `general`)' in text
    for document in (text, (ROOT/'README.md').read_text(encoding='utf-8')):
        assert '--purpose plan' in document
        assert 'explicit lower Claude planning requests remain lower' in document
        assert 'task wording' in document and 'inherited' in document


def test_model_and_effort_guidance_matches_the_approved_contract():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    run_skill = skill('run-with-it')
    pair_skill = skill('pair-colleague')
    coordinator = (ROOT / 'assets' / 'sub-coordinator-prompt.md').read_text(encoding='utf-8')
    assert 'requested_effort' in readme and 'requested_effort' in run_skill
    assert 'requested_effort' in coordinator
    assert 'requested effort' in pair_skill and 'effective effort' in pair_skill
    assert 'effective value returned by the shared policy' in run_skill
    assert 'gemini-4-neon-high' in readme
    assert '21 exact IDs' in readme
    assert 'Only Neon Medium' in readme
    assert 'pair read-only eligibility' in readme
    assert 'Only `plan` (the original role, not `routing_level`)' in run_skill
    assert '`--role plan`' in readme
    assert '`--purpose plan`' in pair_skill
    assert 'task wording, difficulty, and inherited planning variables never change purpose' in pair_skill
    assert 'max`/`maximum` to `xhigh`' in pair_skill
    assert 'arguments never emit `max` or `maximum`' in pair_skill
    assert 'Dispatch normalizes it before admission' in coordinator
    assert 'the admitted record\'s `effort` is the effective value' in coordinator

def test_pair_templates_share_exact_sections_and_status_vocabulary():
    decisions=fences(skill('pair-colleague'),'markdown')
    assert any(re.findall(r'^# (.+)$',body,re.M)==pair_documents.DECISION_SECTIONS for body in decisions)
    prompt=resource('pair-colleague-agent-prompt.md')
    for status in pair_documents.AGENT_STATUS_VALUES:assert 'DISCUSSION_STATUS: '+status in prompt
    for action in pair_documents.DECISION_ACTION_VALUES:assert 'COORDINATOR_ACTION: '+action in skill('pair-colleague')


def test_platform_and_safe_permission_guidance_uses_native_runtime():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    for text in (readme,skill('pair-colleague'),skill('run-with-it')):
        assert 'Windows' in text and 'Linux' in text and 'macOS' in text
        assert 'ai-skills' in text
        assert 'Bash only' not in text
    from ai_skills.registry import load_registry
    registry=load_registry()
    assert registry['agents']['codex']['permission_modes']['read_only']=='--sandbox=read-only'
    assert 'read-only' in skill('pair-colleague')


def test_merge_recovery_bootstrap_has_no_unrelated_implementation_skills():
    text=resource('merge-recovery-prompt.md')
    assert 'tdd-implementation' not in text and 'both activations' not in text
    assert 'ai-skills' in text


def test_coordinator_owns_ordinary_implementation_capture_checks():
    instructions = section(resource('sub-coordinator-prompt.md'), 'Native Worker Configuration')
    assert 'complete list of applicable approved checks' in instructions
    assert 'all approved command and manual checks' in instructions
    assert 'any supported enabled route' in instructions
    assert 'dispatcher selects the adapter' in instructions
    assert 'omit `result_capture`' in instructions


def test_coordinator_binds_modifier_capture_to_finalized_review_cycle():
    text = (ROOT / 'assets/sub-coordinator-prompt.md').read_text(encoding='utf-8')
    revise = text.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    for phrase in ('exactly the comment IDs of the finalized current-cycle review', 'no severity filtering, generated IDs, deduplication or earlier-cycle IDs', 'full findings stay in', 'baseline applicable checks', 'additional review-required checks', 'finalized successful review attempt', 'review_head_sha', 'sub-state.json', 'never advances the review cycle', 'REVIEW_HEAD_SHA=MODIFY_COMMIT_SHA', 'REVIEW_BASE_SHA` stays as `ISSUE_BASE_SHA'):
        assert phrase in revise
    assert 'only permitted read' in text and 'comments[].id' in text and 'comments[].verification' in text
    assert 'Never read `REVIEWER_INSTRUCTIONS_FILE`' not in text
    modifier = (ROOT / 'assets/modifier-prompt.md').read_text(encoding='utf-8')
    for phrase in ('review_comment_ids` is the finalized current-cycle checklist', 'from `REVIEWER_INSTRUCTIONS_FILE`', 'never advances the review cycle', 'exact blocker', 'smallest next action'):
        assert phrase in modifier


def test_coordinator_keeps_resolved_mandatory_checks_in_file_fallback():
    text = resource('sub-coordinator-prompt.md')
    revise = text.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    for phrase in ('resolved mandatory checks', 'whether or not `result_capture` is included', 'illustrative verification suggestions are not'):
        assert phrase in revise


def test_coordinator_reuses_the_saved_review_head_after_either_modifier_noop():
    text = resource('sub-coordinator-prompt.md')
    revise = text.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    noop = revise.split('**Verified no-op**', 1)[1].split('**Committed or preserved success**', 1)[0]
    assert '`no_op=true`, `commit_sha=NONE` and `verification.passed=true`' in noop
    assert 'captured no-op also requires `verification.commit_sha`' in noop
    assert 'file-published no-op may omit that field' in noop
    assert 'Keep the saved `REVIEW_HEAD_SHA` and leave `MODIFY_COMMIT_SHA` unchanged' in noop
    assert 'set `REVIEW_HEAD_SHA` from the saved `review_head_sha`' in revise
    assert '`REVIEW_HEAD_SHA=<saved review_head_sha>`' in revise
    assert '`REVIEW_HEAD_SHA=<saved review_head_sha>`' in text.split('**`verdict=revise`**', 1)[0]
    recovery = section(text, 'Recovery Mode')
    assert 'apply the post-dispatch modifier outcome rules under `verdict=revise`' in recovery
    assert 'completed `modify` worker should update' not in recovery
    persistence = section(text, 'Appendix D: Sub-Coordinator State (Compaction Survival)')
    assert 'persist the state from the post-dispatch rules under `verdict=revise`' in persistence
    assert 'store `modify_commit_sha` and update `review_head_sha = modify_commit_sha`' not in persistence


def test_coordinator_stops_on_typed_blocked_before_modifier_success_gates():
    text = resource('sub-coordinator-prompt.md')
    revise = text.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    outcomes = revise.split('**Post-dispatch modifier outcomes**', 1)[1]
    blocked, remaining = outcomes.split('**Typed `failed` outcome**', 1)
    assert '**Typed `blocked` outcome**' in blocked
    assert 'smallest next action' in blocked and '`blocking_reasons`' in blocked
    assert 'preserve the saved cycle and review range' in blocked
    assert 'End this path immediately before any commit or passing-verification checks' in blocked
    assert 'do not enter `failed-review` or Worker Artifact Recovery' in blocked
    assert '**Actual handoff failure**' in remaining
    assert 'Only verified success reaches this step' in remaining
    assert "if the modification agent's output does not include passing verification results" not in revise


def test_modifier_carries_prior_review_invariants_and_redesign_checkpoint():
    modifier = resource('modifier-prompt.md')
    inputs = section(modifier, 'Inputs Expected')
    assert 'PRIOR_REVIEW_LEDGER' in inputs and 'exactly the current cycle' in inputs
    boundary = section(modifier, 'Invariant & Boundary Check')
    for phrase in ('First delivery and redelivery', 'Real persistence, not only mocks', 'Every sibling caller',
                   'Earlier closures stay closed', 'Integration proof at boundaries'):
        assert phrase in boundary, phrase
    redesign = section(modifier, 'Redesign Checkpoint')
    assert 'RUN_WITH_IT_REDESIGN_REQUIRED=1' in redesign and "current cycle's checklist only" in redesign
    coordinator = resource('sub-coordinator-prompt.md')
    assert 'On modification cycle ≥ 2 also pass `PRIOR_REVIEW_LEDGER`' in coordinator
    assert '`review_comment_ids` still lists only the current cycle' in coordinator
    assert 'STATUS|type=redesign-checkpoint' in coordinator and 'non_approval_count` reaches 3' in coordinator


def test_test_fidelity_self_check_is_synced_between_worker_prompts():
    impl = section(resource('prompt.md'), 'Test-Fidelity Self-Check')
    modify = section(resource('modifier-prompt.md'), 'Test-Fidelity Self-Check')
    strip = lambda text: re.sub(r'<!-- SYNC:.*?-->', '', text)
    assert strip(impl) == strip(modify)
    for phrase in ('used verbatim', 'Assert terminal state, not absence', 'real readiness signal',
                   'Fixtures mirror production inputs', 'Size limits apply to tests'):
        assert phrase in impl, phrase


def test_reviewer_sweeps_siblings_and_reads_incremental_fix_diff():
    review = resource('review-prompt.md')
    assert '### Sibling-Callsite & Field-Completeness Sweep' in review
    assert 'Search every caller of the helper' in review and 'Check every field the helper' in review
    assert 'PREV_REVIEW_HEAD_SHA..REVIEW_HEAD_SHA' in review
    assert 'the review scope is still the full `REVIEW_BASE_SHA..REVIEW_HEAD_SHA` diff' in review
    coordinator = resource('sub-coordinator-prompt.md')
    assert 'PREV_REVIEW_HEAD_SHA=<review_head_sha of the previous review cycle>' in coordinator
    assert 'paste diff text into any sentence of the payload' in coordinator


def test_path_scoped_skips_are_documented_and_dispatcher_owned():
    modifier = resource('modifier-prompt.md')
    scoped = section(modifier, 'Path-Scoped Verification')
    assert 'ai-skills artifact check-scope' in scoped and '--working-tree' in scoped and '"skipped": true' in scoped
    assert 'do not set `passed` on a skipped entry' in scoped and 'rejects any other skip' in scoped
    assert 'Path-scoped checks (see **Path-Scoped Verification**) are the only exception' in modifier
    implementer = section(resource('prompt.md'), 'Path-Scoped Verification')
    for scoped_section in (scoped, implementer):
        assert '`result_capture.force_checks` is never skippable' in scoped_section
    assert '--from <ISSUE_BASE_SHA> --to HEAD' in implementer and '--from <ISSUE_BASE_SHA> --working-tree' in implementer
    assert 'do not set `passed` on a skipped entry' in implementer and 'rejects any other skip' in implementer
    protocol = resource('artifact-protocol.md')
    assert 'The one exception is a path-scoped skip' in protocol
    assert '--from <REVIEW_HEAD_SHA>' in protocol and '--from <issue_base_sha>' in protocol
    assert 'not in the contract\'s `force_checks`' in protocol
    coordinator = resource('sub-coordinator-prompt.md')
    assert 'Keep every applicable check even when the repository has a `.ai-skills-check-scopes.json` map' in coordinator
    assert 'Implementation attempts never skip checks' not in coordinator
    assert 'an implementation over its whole diff from `issue_base_sha`' in coordinator
    assert '"force_checks": ["<exact required check>"]' in coordinator
    assert 'pass `ISSUE_BASE_SHA=<issue_base_sha>` in the task context' in coordinator
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '.ai-skills-check-scopes.json' in readme and 'read from the issue\'s base commit' in readme
    assert '--working-tree' in readme and 'by induction' in readme
    assert 'Initial implementation attempts never skip checks' not in readme and 'force_checks' in readme


def test_verification_runs_once_on_the_handoff_commit():
    implementer, modifier = resource('prompt.md'), resource('modifier-prompt.md')
    blocks = []
    for text, other in ((implementer, 'modifier-prompt.md'), (modifier, 'prompt.md')):
        order = section(text, 'Verification Order')
        marker = f'<!-- SYNC: intentionally duplicated in assets/{other} '
        assert marker in order
        blocks.append(order.replace(marker, '<!-- SYNC: '))
    assert blocks[0].strip() == blocks[1].strip()
    order = blocks[0]
    assert 'make the handoff commit first' in order
    assert 'run each required check once against that committed `HEAD`' in order
    assert 'Never run a required check on the uncommitted tree and then again after committing the same tree' in order
    assert 'Evidence from an earlier commit does not count for a later one' in order
    assert 'Do not start an unfiltered integration suite' in order
    # Flaky checks: isolate, one rerun of the exact command, then a typed failure.
    assert 'First run only the failing tests in isolation' in order
    assert 'rerun the exact required command **once**' in order
    assert 'An isolated pass never satisfies the required check itself' in order
    assert 'Do not run the check a third time' in order
    # Merge-gate suites are not run per attempt.
    assert '`.maestro/merge-gate.json`) run once at merge' in order
    assert 'the full test suite when it fits the attempt deadline' not in implementer
    assert 'Only proceed here after all tests pass' not in implementer
    assert 'do not run them on the uncommitted tree first' in implementer
    assert 'the required checks then run once on this commit' in modifier
    protocol = resource('artifact-protocol.md')
    assert 'commit first and then run every entry in `result_capture.required_checks` once against the committed HEAD' in protocol
    assert 'Do not also run a required check on the same tree before committing' in protocol
    assert 'rerun the exact required command once' in protocol
    tdd = (ROOT / 'skills/tdd-implementation/SKILL.md').read_text(encoding='utf-8')
    assert 'Re-run the targeted tests for the slice after each refactor step' in tdd
    assert 'one final pass after the last edit' in tdd


def test_required_checks_stay_runnable_scoped_and_out_of_the_merge_gate():
    coordinator = resource('sub-coordinator-prompt.md')
    native = coordinator.split('## Native Worker Configuration', 1)[1].split('## Verification-only retries', 1)[0]
    assert 'Entries are runnable commands or named manual checks' in native
    assert 'Never put a prose scenario' in native
    assert 'verification_plan.final_required_checks' in native and 'include `result_capture` rather than omitting it' in native
    assert '`verification_plan.advisory_not_applicable_notes` are hints: they never remove a check' in native
    assert 'leave out of `required_checks` any command that file already declares' in native
    assert '`ai-skills git merge-issue` runs it after approval' in native
    revise = coordinator.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    assert 'becomes a required check only when it is one exact runnable command or a named manual check' in revise
    assert 'starts with `inspect:`' in revise and 'never a reason to omit `result_capture`' in revise
    review = resource('review-prompt.md')
    assert "or 'inspect: <what to check>'" in review
    assert 'recorded as skipped under `.ai-skills-check-scopes.json`' in review
    assert 'only to repeat a passing run on the same commit' in review
    assert 'the prefix `inspect:`' in review and 'as narrow as proves closure' in review
    plan = resource('plan-prompt.md')
    assert '"verification_plan"' in plan and 'it cannot authorize skipping a check' in plan
    for key in artifacts.VERIFICATION_PLAN_KEYS:
        assert f'"{key}"' in plan
    example = next(value for value in map(json.loads, fences(plan, 'json')) if 'verification_plan' in value)
    assert artifacts.valid_plan_payload(example)
    assert 'Checks declared here run once per issue at merge' in (ROOT / 'README.md').read_text(encoding='utf-8')
