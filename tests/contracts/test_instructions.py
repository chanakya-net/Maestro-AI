"""Active instructions target the single installed native runtime on every OS."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
ACTIVE=[*sorted((ROOT/'assets').glob('*.md')),ROOT/'skills/run-with-it/SKILL.md',ROOT/'skills/pair-colleague/SKILL.md']


def test_active_instructions_have_no_legacy_runtime_entrypoints():
    bad=[]
    pattern=re.compile(r'(?:run-agent|worker-watch|pair-colleague|run-with-it-[\w-]+)\.(?:sh|ps1|py)\b|install\.(?:sh|ps1)\b')
    for path in ACTIVE:
        for number,line in enumerate(path.read_text().splitlines(),1):
            if pattern.search(line):bad.append(f'{path.relative_to(ROOT)}:{number}: {line}')
    assert bad==[],'\n'.join(bad)


def test_os_specific_orchestration_recipes_are_removed():
    for path in ACTIVE:
        text=path.read_text()
        assert not re.search(r'```(?:powershell|bash|sh)\b',text),str(path)
        assert not re.search(r'(?:ConvertFrom-Json|ConvertTo-Json|Start-Process|ForEach-Object|set -euo|\bnohup\b|\bsetsid\b)',text),str(path)


def test_critical_authorities_and_role_schema_contracts_remain():
    coordinator=(ROOT/'assets/coordinator-rules.md').read_text().lower()
    sub=(ROOT/'assets/sub-coordinator-prompt.md').read_text().lower()
    main=(ROOT/'assets/main-orchestrator-rules.md').read_text().lower()
    skill=(ROOT/'skills/run-with-it/SKILL.md').read_text().lower()
    for text in (coordinator,sub,main,skill):
        assert 'sub_coord_agent' in text and 'forced_agent' in text
        assert 'artifact' in text and 'verification' in text
    for name in ('complexity-prompt.md','plan-prompt.md','prompt.md','review-prompt.md','modifier-prompt.md'):
        text=(ROOT/'assets'/name).read_text()
        assert '```json' in text,name
    assert 'cumulative' in sub and 'sticky' in sub
    assert 'infrastructure' in sub and 'capability' in sub
    assert 'compaction' in sub and 'authorit' in sub
    assert 'check-in' in sub


def test_native_cli_examples_are_documented():
    text='\n'.join(path.read_text() for path in ACTIVE)
    for command in ('agent run','worker dispatch','worker inspect','run pool','run watch','run stop','state','route','artifact','github','report','pair','git prepare-issue','git merge-issue'):
        assert 'ai-skills '+command in text,command


def test_model_effort_guidance_keeps_explicit_planning_and_provenance():
    readme = (ROOT/'README.md').read_text()
    run_skill = (ROOT/'skills/run-with-it/SKILL.md').read_text()
    pair_skill = (ROOT/'skills/pair-colleague/SKILL.md').read_text()
    coordinator = (ROOT/'assets/sub-coordinator-prompt.md').read_text()
    assert '`--role plan`' in readme and 'omitting `--role plan` uses `high`' in readme
    assert '`--purpose plan`' in pair_skill and 'default `general`' in pair_skill
    assert 'requested_effort' in readme and 'requested_effort' in run_skill
    assert 'requested_effort' in coordinator
    assert 'the admitted record\'s `effort` is the effective value' in coordinator
    assert 'Only `plan` (the original role, not `routing_level`)' in run_skill
    assert 'Neither task wording nor inherited planning variables change it.' in readme


def test_all_documented_json_objects_parse_and_authority_twins_match():
    import json
    authority=[]
    for path in ACTIVE:
        text=path.read_text()
        for match in re.finditer(r'```json\n(.*?)\n```',text,re.S):
            assert isinstance(json.loads(match.group(1)),dict),str(path)
        match=re.search(r'<!-- NATIVE_AUTHORITY_START:.*?-->\n(.*?)<!-- NATIVE_AUTHORITY_END -->',text,re.S)
        if match:authority.append(match.group(1))
    assert len(authority)==4
    assert len(set(authority))==1


def test_documented_worker_json_validates_with_fake_provider(tmp_path,capsys):
    import json
    import os
    import sys
    from ai_skills.dispatch import WorkerConfig,main
    text=(ROOT/'assets/sub-coordinator-prompt.md').read_text()
    examples=[json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```',text,re.S)]
    worker=next(value for value in examples if 'context_file' in value and 'agent' in value)
    for key,value in tuple(worker.items()):
        if isinstance(value,str) and value.startswith('/absolute/'):
            worker[key]=str(tmp_path/key)
    worker['repo_root']=str(tmp_path)
    for key in ('context_file','prompt_file'):
        Path(worker[key]).write_text('fixture task',encoding='utf-8')
    registry=tmp_path/'registry.json'
    registry.write_text(json.dumps({'agents':{'codex':{'invocation':{'command':sys.executable,'args_template':['-c','raise AssertionError("validation must not launch")']},'permission_modes':{'default':''}}}}))
    worker['registry_file']=str(registry)
    config=tmp_path/'worker.json';config.write_text(json.dumps(worker))
    assert WorkerConfig.from_sources(worker,os.environ).agent_run.role=='impl'
    assert main(['dispatch','--config',str(config),'--validate-only'])==0
    assert not Path(worker['state_file']).exists()


def test_documented_pool_json_matches_native_configuration():
    import json
    from ai_skills.pool import PoolConfig
    text=(ROOT/'skills/run-with-it/SKILL.md').read_text()
    examples=[json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```',text,re.S)]
    sample=next(value for value in examples if 'sub_coord_agent' in value and 'state_file' in value)
    config=PoolConfig.from_record(sample)
    assert config.sub_coord_agent=='codex' and config.detach is True


def test_native_command_families_accept_help_without_launching():
    import subprocess
    import sys
    for command in [('agent','run'),('worker','dispatch'),('worker','inspect'),('worker','emit'),('run','pool'),('run','watch'),('run','stop'),
                    ('state',),('route',),('artifact',),('github',),('report',),('pair',),('git','prepare-issue'),('git','merge-issue')]:
        result=subprocess.run([sys.executable,'-m','ai_skills',*command,'--help'],cwd=ROOT,capture_output=True,text=True)
        assert result.returncode==0,(command,result.stderr)


ROLE_PROMPTS=('prompt.md','modifier-prompt.md','complexity-prompt.md','plan-prompt.md',
              'review-prompt.md','artifact-recovery-prompt.md','merge-recovery-prompt.md','sub-coordinator-prompt.md')


def test_every_managed_role_references_shared_failure_and_done_contract():
    for name in ROLE_PROMPTS:
        text=(ROOT/'assets'/name).read_text()
        assert 'artifact-protocol.md' in text, name
        assert 'publish failure even without a commit' in text.lower(), name
        assert 'managed done is dispatcher-owned' in text.lower(), name
        assert 'status=success|source=agent' not in text, name
    for path in (ROOT/'assets/coordinator-rules.md', ROOT/'assets/main-orchestrator-rules.md', ROOT/'skills/run-with-it/SKILL.md'):
        text=path.read_text()
        assert 'validated outcome' in text.lower(), path
        assert 'raw process success' in text.lower(), path


def test_managed_payload_appends_one_protocol_with_authoritative_metadata(tmp_path):
    import json
    from ai_skills.runner import AgentRunConfig, _payload
    prompt=tmp_path/'prompt.md';prompt.write_text('role instructions')
    context=tmp_path/'context.md';context.write_text('run_id=forged-context-id')
    base=AgentRunConfig(agent='codex',model='test',repo_root=tmp_path,prompt_file=prompt,context_file=context,
        state_file=tmp_path/'worker.state.json',result_file=tmp_path/'review-status.json',
        role='review',issue='7',run_id='real-run',attempt_id='real-attempt')
    standalone=_payload(base)
    assert 'Managed artifact protocol' not in standalone
    payload=_payload(base.replace(dispatcher_managed=True))
    assert payload.count('# Managed artifact protocol') == 1
    metadata=json.loads(payload.split('Authoritative attempt metadata:\n',1)[1].split('\n\n',1)[0])
    assert metadata == {'run_id':'real-run','attempt_id':'real-attempt','issue':'7','role':'review',
        'state_file':str((tmp_path/'worker.state.json').resolve()),
        'expected_artifacts':{'result':str((tmp_path/'review-status.json').resolve()),
        'review_instructions':str((tmp_path/'review-instructions.json').resolve())}}
    assert payload.index('role instructions') < payload.index('# Managed artifact protocol')


def test_shared_protocol_failure_examples_validate_and_apply_without_success_companions():
    import json
    from ai_skills.artifacts import role_failure_reason
    text=(ROOT/'assets/artifact-protocol.md').read_text()
    examples=[json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```',text,re.S)]
    failures=[item for item in examples if item.get('artifact_kind')=='role_failure']
    assert {item['status'] for item in failures} == {'failed','blocked','interrupted'}
    for item in failures:
        assert role_failure_reason(item,issue='7',role='impl',run_id='run-7',attempt_id='attempt-1') == ''
        assert 'commit_sha' not in item and item.get('verification',{}).get('passed') is not True
    assert 'verification.passed=true' in text
    assert 'Do not withhold' in text and 'failure' in text
    assert 'review instructions' in text and 'plan.md' in text


def test_capture_failure_instructions_preserve_stop_and_file_precedence():
    import json
    from ai_skills.artifacts import role_failure_reason

    protocol = (ROOT / 'assets/artifact-protocol.md').read_text()
    prompt = (ROOT / 'assets/prompt.md').read_text()
    modifier = (ROOT / 'assets/modifier-prompt.md').read_text()
    for instructions in (protocol, prompt, modifier):
        assert 'existing result path takes precedence' in instructions
        assert 'pending attempt' in instructions and 'confirmed owned-tree shutdown' in instructions
        assert 'zero provider and runner exit codes' in instructions and 'no runner failure' in instructions
        assert 'stop intent prevents capture import even after a zero exit' in instructions.lower()
        assert re.search(r'If a stop arrives and tools remain available, publish .*interrupted.* directly with '
                         r'`ai-skills artifact write-json`', instructions)
        assert 'DONE' in instructions and 'dispatcher' in instructions
    examples = [json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```', protocol, re.S)]
    captured_failures = [item for item in examples if item.get('artifact_kind') == 'captured_result'
                         and item['result'].get('artifact_kind') == 'role_failure']
    assert {item['result']['status'] for item in captured_failures} == {'failed', 'blocked'}
    for item in captured_failures:
        identity = {key: item[key] for key in ('issue', 'role', 'run_id', 'attempt_id')}
        report = item['result']
        assert role_failure_reason(report, **identity) == ''
        assert report['evidence'] and 'commit_sha' not in report


def test_shared_protocol_preserves_coordinator_ledger_ownership():
    text=(ROOT/'assets/artifact-protocol.md').read_text()
    assert 'dispatcher-owned `state_file` and its claim/config/outcome records' in text
    assert "another worker's state" in text
    assert 'Sub-Coordinators must write their own issue `sub-state.json`' in text
    assert 'Never create, edit, repair or remove' in text
    assert 'DONE, runtime state' not in text


def test_merge_recovery_reports_failed_verification_without_claiming_completion():
    text=(ROOT/'assets/merge-recovery-prompt.md').read_text()
    assert 'Never publish failed verification.' not in text
    assert 'Never publish a completed recovery report with failed verification' in text
    assert 'report failed-merge/blocked with the actual failure evidence' in text
    assert 'publish failure even without a commit' in text


def test_modifier_capture_contract_overrides_legacy_reporting():
    import json
    from ai_skills.artifacts import review_comment_closure_reason, role_failure_reason

    modifier = (ROOT / 'assets/modifier-prompt.md').read_text()
    protocol = (ROOT / 'assets/artifact-protocol.md').read_text()
    section = modifier.split('### Captured handoff', 1)[1].split('\n## ', 1)[0]
    assert 'result_capture' in section and 'review_comment_ids' in section
    assert 'overrides' in section and 'file publication' in section
    assert '`MODIFY_COMMIT_SHA`' in section and '`<promise>NO_MORE_TASKS</promise>`' in section
    assert 'exactly one' in section and 'no fences' in section
    assert '`addressed`' in section and '`declined`' in section and '`blocked`' in section
    assert 'exact blocker' in section and 'smallest next action' in section
    assert 'REVIEW_BASE_SHA' in section and 'pre-spawn' in section
    assert 'verification_contract' in section
    for text in (modifier, protocol):
        assert 'exact blocker' in text and 'smallest next action' in text
    examples = [json.loads(match.group(1)) for text in (modifier, protocol)
                for match in re.finditer(r'```json\n(.*?)\n```', text, re.S)]
    captured = [item for item in examples if item.get('artifact_kind') == 'captured_result'
                and item['role'] == 'modify']
    successes = [item['result'] for item in captured if item['result'].get('status') == 'success']
    blocked = [item for item in captured if item['result'].get('status') == 'blocked']
    assert successes and blocked
    for report in successes:
        ids = [entry['id'] for entry in report['review_comment_closure']]
        assert review_comment_closure_reason(report, ids) == ''
        assert {entry['status'] for entry in report['review_comment_closure']} == {'addressed', 'declined'}
    for item in blocked:
        identity = {key: item[key] for key in ('issue', 'role', 'run_id', 'attempt_id')}
        assert role_failure_reason(item['result'], **identity) == ''
        assert 'next action' in item['result']['summary'].lower()


def test_ordinary_impl_capture_uses_selected_enabled_route():
    import json
    from ai_skills.dispatch import WorkerConfig

    text = (ROOT / 'assets/sub-coordinator-prompt.md').read_text(encoding='utf-8')
    section = text.split('## Native Worker Configuration', 1)[1].split('## Verification-only retries', 1)[0]
    example = json.loads(re.search(r'```json\n(.*?)\n```', section, re.S)[1])
    for agent in ('codex', 'claude', 'agy', 'opencode'):
        config = WorkerConfig.from_sources({**example, 'agent': agent}, {})
        assert config.agent_run.role == 'impl'
        assert config.result_capture == {'required_checks': ['<approved command or manual check>'], 'review_comment_ids': []}
        assert config.agent_run.result_capture_context is None
    protocol = (ROOT / 'assets/artifact-protocol.md').read_text(encoding='utf-8')
    assert 'overrides file-only reporting, `IMPL_COMMIT_SHA` output and completion suffixes' in protocol
    assert '`verification_contract` takes precedence' in protocol


def test_ordinary_modifier_capture_uses_selected_enabled_route():
    import json
    from ai_skills.dispatch import WorkerConfig

    text = (ROOT / 'assets/sub-coordinator-prompt.md').read_text(encoding='utf-8')
    section = text.split('## Native Worker Configuration', 1)[1].split('## Verification-only retries', 1)[0]
    blocks = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', section, re.S)]
    modify = next(block for block in blocks if block.get('role') == 'modify')
    example = {**blocks[0], **modify}
    assert 'agent' not in modify
    for agent in ('codex', 'claude', 'agy', 'opencode'):
        config = WorkerConfig.from_sources({**example, 'agent': agent}, {})
        assert config.agent_run.role == 'modify' and config.agent_run.cycle == '2'
        assert config.result_capture == {'required_checks': ['<approved command or manual check>', '<additional review-required check>'], 'review_comment_ids': ['R001', 'R002']}
        assert config.agent_run.result_capture_context is None
        assert config.issue_base_sha == blocks[0]['issue_base_sha'] and config.pre_spawn_head == ''
    assert modify['prompt_file'].endswith('modifier-prompt.md')
    assert '/workers/modify/' in modify['result_file']


def test_modifier_capture_falls_back_to_file_contract_on_ambiguous_ids():
    import json
    import pytest
    from ai_skills.dispatch import WorkerConfig

    text = (ROOT / 'assets/sub-coordinator-prompt.md').read_text(encoding='utf-8')
    section = text.split('## Native Worker Configuration', 1)[1].split('## Verification-only retries', 1)[0]
    blocks = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', section, re.S)]
    example = {**blocks[0], **next(b for b in blocks if b.get('role') == 'modify')}
    assert WorkerConfig.from_sources({k: v for k, v in example.items() if k != 'result_capture'}, {}).result_capture is None
    for ids in (['R001', 'R001'], ['R001', ' '], 'R001'):
        with pytest.raises(ValueError):
            WorkerConfig.from_sources({**example, 'result_capture': {'required_checks': ['check'], 'review_comment_ids': ids}}, {})
    with pytest.raises(ValueError):
        WorkerConfig.from_sources({**example, 'result_capture': {'required_checks': [], 'review_comment_ids': ['R001']}}, {})
    revise = text.split('**`verdict=revise`**', 1)[1].split('**`verdict=reject`**', 1)[0]
    for phrase in ('IDs repeat', 'review-required check cannot be named exactly', 'use the existing file contract', 'dispatcher selects the adapter'):
        assert phrase in revise
