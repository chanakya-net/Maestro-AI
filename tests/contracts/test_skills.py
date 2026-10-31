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


def test_plan_example_is_a_valid_nonempty_plan_and_remains_read_only():
    text=resource('plan-prompt.md')
    plans=[value for value in json_examples(text) if value.get('role')=='plan']
    assert plans and all(artifacts.valid_plan_payload(value) for value in plans)
    for value in plans:
        assert value['slices'] and value['complexity_level'] in routing.BAND_ORDER
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
    registry=json.loads(resource('agent-registry.json'))
    assert registry['agents']['codex']['permission_modes']['read_only']=='--sandbox=read-only'
    assert 'read-only' in skill('pair-colleague')


def test_merge_recovery_bootstrap_has_no_unrelated_implementation_skills():
    text=resource('merge-recovery-prompt.md')
    assert 'tdd-implementation' not in text and 'both activations' not in text
    assert 'ai-skills' in text
