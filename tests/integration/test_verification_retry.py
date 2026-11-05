"""Preserved-commit retries through real Git, providers and dispatcher journals."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_skills.dispatch import WorkerConfig, adopt_worker, dispatch_worker
from ai_skills.runner import AgentRunConfig
from ai_skills.storage import load_json


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def attempt(tmp_path, repo, name, source, role='impl', cycle='1', **options):
    directory = tmp_path / name
    directory.mkdir()
    script = directory / 'provider.py'
    script.write_text(source, encoding='utf-8')
    registry = directory / 'registry.json'
    registry.write_text(json.dumps({'agents': {'fake': {'invocation': {
        'command': sys.executable, 'args_template': [str(script), '{{prompt}}']},
        'permission_modes': {'default': ''}}}}))
    context = directory / 'context.md'; context.write_text('Verify the issue acceptance criteria.')
    prompt = directory / 'prompt.md'; prompt.write_text('Use the authoritative attempt contract.')
    run = AgentRunConfig(agent='fake', context_file=context, prompt_file=prompt,
        repo_root=repo, registry_file=registry, role=role, issue='7', cycle=cycle,
        issue_dir=tmp_path / 'issue', log_file=directory / 'worker.log',
        done_file=directory / 'worker.done', result_file=directory / 'result.json',
        record_directory=tmp_path / 'processes', heartbeat_seconds=.03,
        environment=dict(os.environ), gui_mode='0')
    record = WorkerConfig(agent_run=run, state_file=directory / 'worker.state.json', poll_seconds=.02).to_record()
    return WorkerConfig.from_sources({**record, **options}, os.environ)


COMMIT_THEN_BLOCK = '''import json, os, subprocess
from pathlib import Path
Path('feature.txt').write_text('implemented\\n')
subprocess.run(['git', 'add', 'feature.txt'], check=True)
subprocess.run(['git', 'commit', '-qm', 'Implement feature'], check=True)
report={'schema_version':1,'artifact_kind':'role_failure','issue':'7',
    'role':os.environ['RUN_WITH_IT_ROLE'],'run_id':os.environ['RUN_WITH_IT_RUN_ID'],
    'attempt_id':os.environ['RUN_WITH_IT_ATTEMPT_ID'],'status':'blocked',
    'reason':'verification-service-unavailable','summary':'Committed; verification unavailable.'}
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps(report))
'''

VERIFY = '''import json, os, subprocess, sys
from pathlib import Path
from ai_skills.artifacts import main
metadata=json.loads(sys.argv[1].split('Authoritative attempt metadata:\\n',1)[1])
assert metadata['verification_contract']['execution_mode']=='verify_existing'
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
assert Path('feature.txt').read_text()=='implemented\\n'
payload={'schema_version':1,'issue':'7','role':os.environ['RUN_WITH_IT_ROLE'],
    'status':'success','execution_mode':'verify_existing','commit_sha':head,
    'files_committed':['feature.txt'],'verification':{'passed':True,'commit_sha':head,
    'checks':[{'command':'feature acceptance','passed':True,'evidence':'feature.txt contains implemented'}]}}
payload_path=Path(os.environ['RUN_WITH_IT_RESULT_FILE']).with_name('payload.json')
payload_path.write_text(json.dumps(payload))
sys.exit(main(['write-json','--role',payload['role'],'--issue','7',
    '--repo-root',os.getcwd(),'--payload-file',str(payload_path),
    '--result-file',os.environ['RUN_WITH_IT_RESULT_FILE'], '--pre-spawn-head',head]))
'''


def preserved(tmp_path, role='impl'):
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-q'); git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'feature.txt').write_text('baseline\n')
    git(repo, 'add', 'feature.txt'); git(repo, 'commit', '-qm', 'Baseline')
    base = git(repo, 'rev-parse', 'HEAD')
    # Modification starts after an implementation commit, but retains issue base.
    if role == 'modify':
        (repo / 'implementation.txt').write_text('initial implementation\n')
        git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'Initial implementation')
    first = attempt(tmp_path, repo, 'first', COMMIT_THEN_BLOCK, role, issue_base_sha=base)
    assert dispatch_worker(first).state == 'failed'
    saved = WorkerConfig.from_record(load_json(Path(str(first.state_file) + '.config.json')))
    head = git(repo, 'rev-parse', 'HEAD')
    contract = {'source_state_file': str(first.state_file), 'source_attempt_id': saved.agent_run.attempt_id,
        'expected_commit_sha': head, 'required_checks': ['feature acceptance']}
    return repo, base, head, first, contract


@pytest.mark.parametrize('role', ['impl', 'modify'])
def test_verify_preserved_commit_after_blocked_attempt(tmp_path, role):
    repo, base, head, first, contract = preserved(tmp_path, role)
    source_bytes = Path(str(first.state_file) + '.outcome.json').read_bytes()
    retry = attempt(tmp_path, repo, 'retry', VERIFY, role, execution_mode='verify_existing',
        issue_base_sha=base, retry_contract=contract)
    result = dispatch_worker(retry)
    assert result.state == 'completed', result.reason
    report = load_json(retry.agent_run.result_file)
    assert report['commit_sha'] == head and not report.get('no_op')
    assert git(repo, 'rev-parse', 'HEAD') == head
    assert git(repo, 'status', '--porcelain') == ''
    saved = load_json(Path(str(retry.state_file) + '.config.json'))
    assert saved['pre_spawn_head'] == head
    assert saved['issue_base_sha'] == base and saved['retry_contract'] == contract
    assert adopt_worker(retry.state_file).state == 'completed'
    assert Path(str(first.state_file) + '.outcome.json').read_bytes() == source_bytes


def retry_config(tmp_path, repo, base, contract, source=VERIFY, **options):
    return attempt(tmp_path, repo, 'retry', source, execution_mode='verify_existing',
        issue_base_sha=base, retry_contract=contract, **options)


@pytest.mark.parametrize('kind', ['issue', 'worktree', 'role', 'cycle', 'attempt', 'head',
    'baseline', 'source-state', 'missing-progress', 'pending-source', 'dirty', 'untracked'])
def test_reject_invalid_provenance_before_launch(tmp_path, kind):
    repo, base, head, first, contract = preserved(tmp_path)
    retry = retry_config(tmp_path, repo, base, contract)
    if kind == 'issue': retry = replace(retry, agent_run=retry.agent_run.replace(issue='8'))
    if kind == 'worktree': retry = replace(retry, agent_run=retry.agent_run.replace(repo_root=tmp_path))
    if kind == 'role': retry = replace(retry, agent_run=retry.agent_run.replace(role='modify'))
    if kind == 'cycle': retry = replace(retry, agent_run=retry.agent_run.replace(cycle='2'))
    if kind == 'attempt': contract['source_attempt_id'] = 'different'
    if kind == 'head': contract['expected_commit_sha'] = base
    if kind == 'baseline': retry = replace(retry, issue_base_sha=head)
    if kind == 'source-state': contract['source_state_file'] = str(tmp_path / 'missing')
    if kind in ('missing-progress', 'pending-source'):
        path = Path(str(first.state_file) + '.outcome.json'); data = load_json(path)
        if kind == 'missing-progress': data.pop('git_progress', None)
        else: data['status'] = 'pending'
        path.write_text(json.dumps(data))
    if kind == 'dirty': (repo / 'feature.txt').write_text('dirty\n')
    if kind == 'untracked': (repo / 'new-source.py').write_text('uncommitted\n')
    with pytest.raises(ValueError): dispatch_worker(retry)
    assert not retry.state_file.exists()
    assert git(repo, 'rev-parse', 'HEAD') == head


@pytest.mark.parametrize('mutation,reason', [
    ("payload['no_op']=True", 'invalid-verification-result'),
    ("payload['commit_sha']='f'*40", 'verification-commit-mismatch'),
    ("payload['verification']['commit_sha']='f'*40", 'verification-evidence-commit-mismatch'),
    ("payload['verification']['passed']=False", 'implementation-verification-failed'),
    ("payload['verification'].pop('checks')", 'verification-checks-incomplete'),
    ("payload['verification']['checks']=[]", 'verification-checks-incomplete'),
    ("payload['verification']['checks']*=2", 'verification-checks-incomplete'),
    ("payload['verification']['checks'][0]['command']='unrelated check'", 'verification-checks-incomplete'),
    ("payload['verification']['checks'][0]['passed']=False", 'verification-check-not-passed'),
    ("payload['verification']['checks'][0]['evidence']=''", 'verification-check-not-passed'),
])
def test_publication_and_finalization_reject_bad_evidence(tmp_path, mutation, reason):
    repo, base, head, first, contract = preserved(tmp_path)
    # Publication must reject; a raw write afterward must not fool finalization.
    source = VERIFY.replace('payload_path.write_text(json.dumps(payload))',
        mutation + '\npayload_path.write_text(json.dumps(payload))')
    source = source.replace('sys.exit(main(', 'code=main(').replace("'--pre-spawn-head',head]))", "'--pre-spawn-head',head])\nassert code==1\nPath(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps(payload))")
    retry = retry_config(tmp_path, repo, base, contract, source)
    result = dispatch_worker(retry)
    assert result.state == 'failed' and result.reason == reason
    assert git(repo, 'rev-parse', 'HEAD') == head


@pytest.mark.parametrize('change,reason', [
    ("Path('feature.txt').write_text('dirty\\n')", 'verification-dirty-worktree'),
    ("Path('new.py').write_text('new source\\n')", 'verification-dirty-worktree'),
    ("subprocess.run(['git','commit','--allow-empty','-qm','unexpected'],check=True)", 'verification-head-changed'),
])
def test_finalization_checks_tree_again_after_publication(tmp_path, change, reason):
    repo, base, head, first, contract = preserved(tmp_path)
    source = VERIFY.replace('sys.exit(main(', 'code=main(').replace("'--pre-spawn-head',head]))", "'--pre-spawn-head',head])\nassert code==0\n" + change)
    retry = retry_config(tmp_path, repo, base, contract, source)
    result = dispatch_worker(retry)
    assert result.state == 'failed' and result.reason == reason
    assert load_json(retry.agent_run.result_file)['commit_sha'] == head


def test_timeout_never_auto_commits_verification_changes(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    source = "from pathlib import Path\nimport time\nPath('feature.txt').write_text('dirty')\ntime.sleep(30)"
    retry = retry_config(tmp_path, repo, base, contract, source, hard_limit_seconds=.5)
    result = dispatch_worker(retry)
    assert result.state == 'failed' and result.reason == 'hard-limit-exceeded'
    assert git(repo, 'rev-parse', 'HEAD') == head
    assert (repo / 'feature.txt').read_text() == 'dirty'
    assert not Path(retry.agent_run.result_file).exists()


def test_verification_publication_never_cherry_picks(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    source = VERIFY.replace('payload_path.write_text(json.dumps(payload))',
        "subprocess.run(['git','checkout','--detach'," + repr(base) + "],check=True)\npayload_path.write_text(json.dumps(payload))")
    retry = retry_config(tmp_path, repo, base, contract, source)
    assert dispatch_worker(retry).state == 'failed'
    assert git(repo, 'rev-parse', 'HEAD') == base
    assert not Path(retry.agent_run.result_file).exists()


def test_ordinary_retry_still_requires_a_new_commit(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    source = VERIFY.replace("assert metadata['verification_contract']['execution_mode']=='verify_existing'", "assert 'verification_contract' not in metadata")
    source = source.replace("'execution_mode':'verify_existing',", '')
    retry = attempt(tmp_path, repo, 'retry', source)
    assert dispatch_worker(retry).state == 'failed'
    assert 'missing-implementation-commit' in Path(retry.agent_run.log_file).read_text()
    assert git(repo, 'rev-parse', 'HEAD') == head


def test_report_cannot_opt_into_verification_without_dispatch_contract(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    source = VERIFY.replace("assert metadata['verification_contract']['execution_mode']=='verify_existing'", "assert 'verification_contract' not in metadata")
    retry = attempt(tmp_path, repo, 'retry', source)
    assert dispatch_worker(retry).state == 'failed'
    assert 'verification-contract-required' in Path(retry.agent_run.log_file).read_text()


def test_restart_repairs_publication_after_outcome_journal(tmp_path, monkeypatch):
    from ai_skills import dispatch
    repo, base, head, first, contract = preserved(tmp_path)
    retry = retry_config(tmp_path, repo, base, contract)
    original = dispatch.write_json_atomic
    def fail_state(path, payload):
        if Path(path) == retry.state_file and payload.get('state') == 'completed':
            raise OSError('crash after outcome journal')
        original(path, payload)
    with monkeypatch.context() as patch:
        patch.setattr(dispatch, 'write_json_atomic', fail_state)
        with pytest.raises(OSError, match='crash after outcome'): dispatch_worker(retry)
    assert load_json(Path(str(retry.state_file) + '.outcome.json'))['status'] == 'success'
    assert adopt_worker(retry.state_file).state == 'completed'
    assert load_json(retry.state_file)['state'] == 'completed'
    assert 'status=success' in Path(retry.agent_run.done_file).read_text()


def test_adoption_after_downstream_progress_keeps_verified_handoff(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    retry = retry_config(tmp_path, repo, base, contract)
    assert dispatch_worker(retry).state == 'completed'
    outcome_path = Path(str(retry.state_file) + '.outcome.json')
    original = outcome_path.read_bytes()
    git(repo, 'commit', '--allow-empty', '-qm', 'Later review modification')
    assert adopt_worker(retry.state_file).state == 'completed'
    assert outcome_path.read_bytes() == original


@pytest.mark.parametrize('role', ['impl', 'modify'])
def test_retry_review_and_merge_handoff_preserves_accumulated_range(tmp_path, role):
    from ai_skills.git_operations import merge_issue
    repo, base, head, first, contract = preserved(tmp_path, role)
    git(repo, 'branch', 'shared', base)
    git(repo, 'branch', 'issue-7', head)
    retry = retry_config(tmp_path, repo, base, contract, role=role)
    assert dispatch_worker(retry).state == 'completed'
    # A deterministic coordinator consumes persisted runtime config and report;
    # the real reviewer dispatcher and native merge operation complete the flow.
    config = load_json(Path(str(retry.state_file) + '.config.json'))
    report = load_json(retry.agent_run.result_file)
    review_context = {'review_base_sha': config['issue_base_sha'], 'review_head_sha': report['commit_sha']}
    reviewer_source = '''import json,os,subprocess,sys
from pathlib import Path
context=json.loads(sys.argv[1].split('\\nInstructions:',1)[0])
files=subprocess.check_output(['git','diff','--name-only',context['review_base_sha'],context['review_head_sha']],text=True).splitlines()
assert 'feature.txt' in files
if os.environ['TEST_MODIFICATION']=='1': assert 'implementation.txt' in files
Path(os.environ['REVIEWER_STATUS_FILE']).write_text(json.dumps({'verdict':'approve','comment_count':0,'nitpick_only':False}))
Path(os.environ['REVIEWER_INSTRUCTIONS_FILE']).write_text(json.dumps({'verdict':'approve','summary':'Accumulated issue diff verified','comments':[],'blocking_reasons':[]}))
'''
    reviewer = attempt(tmp_path, repo, 'review', reviewer_source, role='review')
    Path(reviewer.agent_run.context_file).write_text(json.dumps(review_context))
    reviewer = replace(reviewer, agent_run=reviewer.agent_run.replace(
        result_file=tmp_path / 'review' / 'review-status.json',
        environment={**os.environ, 'TEST_MODIFICATION': '1' if role == 'modify' else '0'}))
    assert dispatch_worker(reviewer).state == 'completed'
    merged = merge_issue(repo, 'shared', 'issue-7', verify_commands=[[
        sys.executable, '-c', "from pathlib import Path; assert Path('feature.txt').read_text()=='implemented\\n'"]])
    assert merged['outcome'] == 'completed'
    assert head in git(repo, 'rev-list', merged['commit_sha']).splitlines()
    assert git(repo, 'rev-parse', 'HEAD') == head
    assert not (tmp_path / 'artifact-recovery').exists()


@pytest.mark.parametrize('target', ['report', 'contract'])
def test_adoption_rejects_changed_verified_handoff(tmp_path, target):
    repo, base, head, first, contract = preserved(tmp_path)
    retry = retry_config(tmp_path, repo, base, contract)
    assert dispatch_worker(retry).state == 'completed'
    journal_path = Path(str(retry.state_file) + '.outcome.json')
    original = journal_path.read_bytes()
    if target == 'report':
        path = Path(retry.agent_run.result_file); payload = load_json(path)
        payload['verification']['checks'][0]['evidence'] = 'changed after finalization'
    else:
        path = Path(str(retry.state_file) + '.config.json'); payload = load_json(path)
        payload['retry_contract']['required_checks'] = ['different required check']
    path.write_text(json.dumps(payload))
    assert adopt_worker(retry.state_file).state == 'unknown'
    assert journal_path.read_bytes() == original


def test_verification_allows_existing_gitignored_test_outputs(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    # Repository-owned ignore policy, established independently of the retry.
    (repo / '.git' / 'info' / 'exclude').write_text('/test-output/\n')
    source = VERIFY.replace("payload_path.write_text(json.dumps(payload))",
        "Path('test-output').mkdir()\nPath('test-output/result.txt').write_text('test output')\npayload_path.write_text(json.dumps(payload))")
    retry = retry_config(tmp_path, repo, base, contract, source)
    assert dispatch_worker(retry).state == 'completed'
    assert git(repo, 'status', '--porcelain') == ''


def test_live_source_attempt_cannot_be_retried(tmp_path):
    import time
    from ai_skills.dispatch import _stop_provider
    repo, base, head, first, contract = preserved(tmp_path)
    live = attempt(tmp_path, repo, 'live', 'import time; time.sleep(30)', issue_base_sha=base, detach=True)
    assert dispatch_worker(live).state == 'running'
    saved = load_json(Path(str(live.state_file) + '.config.json'))
    contract.update(source_state_file=str(live.state_file), source_attempt_id=saved['agent_run']['attempt_id'])
    try:
        retry = retry_config(tmp_path, repo, base, contract)
        with pytest.raises(ValueError, match='verification-source-not-terminal'): dispatch_worker(retry)
        assert not retry.state_file.exists()
    finally:
        admitted = WorkerConfig.from_record(saved)
        _stop_provider(admitted)
        deadline = time.monotonic() + 10
        while adopt_worker(live.state_file).state == 'running' and time.monotonic() < deadline:
            time.sleep(.05)


@pytest.mark.parametrize('patch', [
    {'execution_mode': 'typo'}, {'execution_mode': 'implement'},
    {'retry_contract': {}}, {'issue_base_sha': 'HEAD'},
])
def test_invalid_retry_configuration_is_rejected(tmp_path, patch):
    repo, base, head, first, contract = preserved(tmp_path)
    valid = retry_config(tmp_path, repo, base, contract).to_record()
    with pytest.raises(ValueError):
        WorkerConfig.from_record({**valid, **patch})


@pytest.mark.parametrize('destination', ['done_file', 'log_file', 'status_file', 'events_log', 'tail_state_file'])
@pytest.mark.parametrize('protected', ['result', 'outcome', 'config', 'done', 'log'])
def test_retry_cannot_overwrite_source_artifacts(tmp_path, destination, protected):
    repo, base, head, first, contract = preserved(tmp_path)
    paths = {'result': Path(first.agent_run.result_file), 'done': Path(first.agent_run.done_file),
        'log': Path(first.agent_run.log_file), 'outcome': Path(str(first.state_file) + '.outcome.json'),
        'config': Path(str(first.state_file) + '.config.json')}
    target = paths[protected]; before = target.read_bytes()
    retry = retry_config(tmp_path, repo, base, contract)
    if destination == 'tail_state_file': retry = replace(retry, tail_state_file=target)
    else: retry = replace(retry, agent_run=retry.agent_run.replace(**{destination: target}))
    with pytest.raises(ValueError, match='verification-source-path-collision'): dispatch_worker(retry)
    assert target.read_bytes() == before
    assert not retry.state_file.exists()


@pytest.mark.parametrize('alias', ['hardlink', 'symlink'])
def test_source_artifact_aliases_are_protected(tmp_path, alias):
    repo, base, head, first, contract = preserved(tmp_path)
    target = Path(first.agent_run.result_file); before = target.read_bytes()
    link = tmp_path / 'aliased-source'
    if alias == 'hardlink': os.link(target, link)
    else:
        try: link.symlink_to(target)
        except OSError: pytest.skip('symlink creation unavailable')
    retry = retry_config(tmp_path, repo, base, contract)
    retry = replace(retry, agent_run=retry.agent_run.replace(done_file=link))
    with pytest.raises(ValueError, match='verification-source-path-collision'): dispatch_worker(retry)
    assert target.read_bytes() == before


def test_detached_source_missing_wrapper_evidence_blocks_retry(tmp_path):
    import time
    from ai_skills.dispatch import _provider_record
    repo, base, head, first, contract = preserved(tmp_path)
    # A second ordinary worker commits a distinct change, then blocks.
    source = COMMIT_THEN_BLOCK.replace("'implemented\\n'", "'modified\\n'")
    second = attempt(tmp_path, repo, 'second', source, issue_base_sha=base, detach=True)
    result = dispatch_worker(second)
    deadline = time.monotonic() + 10
    while result.state == 'running' and time.monotonic() < deadline:
        time.sleep(.05); result = adopt_worker(second.state_file)
    assert result.state == 'failed'
    config = WorkerConfig.from_record(load_json(Path(str(second.state_file) + '.config.json')))
    _, supervisor = _provider_record(config)
    from ai_skills.processes import _match
    wrapper_path = supervisor._path(config.agent_run.run_id + '-dispatcher', config.agent_run.attempt_id)
    owner = load_json(wrapper_path)['owner']
    # Tree death precedes the supervisor owner's last journal write. Wait for
    # that writer too, so deleting evidence cannot race its final publication.
    deadline = time.monotonic() + 10
    while _match(owner) == 'alive' and time.monotonic() < deadline:
        time.sleep(.02)
    assert _match(owner) == 'dead'
    wrapper_path.unlink()
    contract.update(source_state_file=str(second.state_file), source_attempt_id=config.agent_run.attempt_id,
        expected_commit_sha=git(repo, 'rev-parse', 'HEAD'))
    retry = retry_config(tmp_path, repo, base, contract)
    with pytest.raises(ValueError, match='verification-source-dispatcher'): dispatch_worker(retry)
    assert not retry.state_file.exists()


@pytest.mark.parametrize('kind', ['dirty', 'head', 'missing', 'malformed', 'provider-exit'])
def test_terminal_failure_replays_after_tree_recovery(tmp_path, kind):
    repo, base, head, first, contract = preserved(tmp_path)
    if kind in ('missing', 'malformed'):
        source = "import os\nfrom pathlib import Path\nPath('feature.txt').write_text('dirty')"
        if kind == 'malformed': source += "\nPath(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{')"
    else:
        source = VERIFY.replace('sys.exit(main(', 'code=main(').replace("'--pre-spawn-head',head]))", "'--pre-spawn-head',head])\nassert code==0")
        source += {'dirty': "\nPath('feature.txt').write_text('dirty')",
            'head': "\nsubprocess.run(['git','commit','--allow-empty','-qm','unexpected'],check=True)",
            'provider-exit': '\nsys.exit(7)'}[kind]
    retry = retry_config(tmp_path, repo, base, contract, source)
    result = dispatch_worker(retry)
    assert result.state == 'failed'
    path = Path(str(retry.state_file) + '.outcome.json'); before = path.read_bytes()
    git(repo, 'reset', '--hard', head)
    adopted = adopt_worker(retry.state_file)
    assert (adopted.state, adopted.reason, adopted.return_code) == (result.state, result.reason, result.return_code)
    assert path.read_bytes() == before


def test_failed_report_cannot_be_replaced_after_journaling(tmp_path):
    repo, base, head, first, contract = preserved(tmp_path)
    retry = retry_config(tmp_path, repo, base, contract, "pass")
    assert dispatch_worker(retry).state == 'failed'
    path = Path(str(retry.state_file) + '.outcome.json'); before = path.read_bytes()
    Path(retry.agent_run.result_file).write_text('{}')
    assert adopt_worker(retry.state_file).state == 'unknown'
    assert path.read_bytes() == before


def test_verify_existing_ignores_well_formed_capture_for_codex(tmp_path, monkeypatch):
    from ai_skills import dispatch

    repo, base, head, _, contract = preserved(tmp_path)
    source = VERIFY.replace("sys.argv[1].split", "sys.stdin.read().split")
    source = source.replace("head=subprocess", "assert 'result_capture' not in metadata\n"
                            "assert '--output-last-message' not in sys.argv\nhead=subprocess")
    retry = retry_config(tmp_path, repo, base, contract, source=source,
        result_capture={'required_checks': ['unrelated capture check'], 'review_comment_ids': []})
    run = retry.agent_run.replace(agent='codex')
    registry = load_json(Path(run.registry_file))
    registry['agents']['codex'] = registry['agents'].pop('fake')
    invocation = registry['agents']['codex']['invocation']
    invocation['prompt_transport'] = 'stdin'
    invocation['args_template'][-1] = '-'
    Path(run.registry_file).write_text(json.dumps(registry), encoding='utf-8')

    def unexpected_probe(_):
        pytest.fail('verification-only attempt probed Codex capture')

    monkeypatch.setattr(dispatch, 'supports_final_response_capture', unexpected_probe)
    assert dispatch_worker(replace(retry, agent_run=run)).state == 'completed'
    assert load_json(Path(run.result_file))['commit_sha'] == head


def rejected_capture(tmp_path, role):
    from test_dispatch import (BUILD_CAPTURE, CAPTURE_CHECKS, WRITE_CAPTURE,
                               build_modify_capture, capture_worker, modify_worker)
    body = (BUILD_CAPTURE if role == 'impl' else build_modify_capture())
    body += "\nenvelope['attempt_id'] = 'stale'\n" + WRITE_CAPTURE
    source = (capture_worker(tmp_path, body, result_capture=CAPTURE_CHECKS) if role == 'impl'
              else modify_worker(tmp_path, body))
    result = dispatch_worker(source)
    return source, result


@pytest.mark.parametrize('role', ['impl', 'modify'])
def test_rejected_capture_enters_existing_recovery(tmp_path, role):
    import re
    source, result = rejected_capture(tmp_path, role)
    assert (result.state, result.return_code, result.reason) == (
        'artifact-recovery-required', 75, 'artifact-recovery-required')
    saved = load_json(Path(str(source.state_file) + '.config.json'))
    outcome = load_json(Path(str(source.state_file) + '.outcome.json'))
    head = git(source.agent_run.repo_root, 'rev-parse', 'HEAD')
    assert outcome['status'] == 'failed' and outcome['reason'] == 'artifact-recovery-required'
    assert outcome['git_progress'] == {'head': head, 'pre_spawn_head': saved['pre_spawn_head'],
                                       'issue_base_sha': source.issue_base_sha}
    assert load_json(source.agent_run.result_file)['status'] == 'artifact_recovery_required'
    lines = Path(source.agent_run.events_log).read_text().splitlines()
    event, = [line for line in lines if 'type=dispatch-capture-rejected|' in line]
    for field in (f"run_id={saved['agent_run']['run_id']}", f"attempt_id={saved['agent_run']['attempt_id']}",
                  'issue=7', f'role={role}', 'cycle=4', 'reason=capture-identity-mismatch'):
        assert field in event
    assert re.search(r'\|reason=[a-z0-9-]+$', event)
    assert all(text not in event for text in ('observed exit 0', 'Fixed R001', '{', '"'))
    capture = Path(saved['agent_run']['final_response_file'])
    assert capture.read_bytes() == source.agent_run.result_file.with_name('capture-original').read_bytes()
    assert source.state_file.with_name('calls').read_text() == 'run\n'


@pytest.mark.parametrize('role', ['impl', 'modify'])
def test_rejected_capture_source_admits_verification_only_retry(tmp_path, role):
    source, result = rejected_capture(tmp_path, role)
    assert result.state == 'artifact-recovery-required'
    saved = load_json(Path(str(source.state_file) + '.config.json'))
    repo, base = source.agent_run.repo_root, source.issue_base_sha
    head = git(repo, 'rev-parse', 'HEAD')
    paths = [Path(str(source.state_file) + '.outcome.json'), source.agent_run.result_file,
             Path(saved['agent_run']['final_response_file'])]
    originals = [path.read_bytes() for path in paths]
    contract = {'source_state_file': str(source.state_file), 'source_attempt_id': saved['agent_run']['attempt_id'],
                'expected_commit_sha': head, 'required_checks': ['feature acceptance']}
    verify = VERIFY.replace("Path('feature.txt').read_text()=='implemented\\n'",
                            "Path('feature.txt').read_text().startswith('implemented')")
    if role == 'modify':
        verify = verify.replace("'files_committed':['feature.txt']", "'files_committed':['feature.txt','review.txt']")
    retry = attempt(tmp_path, repo, 'retry', verify, role, cycle=4, execution_mode='verify_existing',
                    issue_base_sha=base, retry_contract=contract)
    result = dispatch_worker(retry)
    assert result.state == 'completed', result.reason
    report = load_json(retry.agent_run.result_file)
    assert report['commit_sha'] == head and not report.get('no_op')
    assert git(repo, 'rev-parse', 'HEAD') == head and git(repo, 'status', '--porcelain') == ''
    retry_saved = load_json(Path(str(retry.state_file) + '.config.json'))
    assert retry_saved['pre_spawn_head'] == head and retry_saved['issue_base_sha'] == base
    assert retry_saved['agent_run']['role'] == role and retry_saved['agent_run']['cycle'] == 4
    assert git(repo, 'diff', '--name-only', f'{base}..{head}').splitlines() == (
        ['feature.txt'] if role == 'impl' else ['feature.txt', 'review.txt'])
    assert [path.read_bytes() for path in paths] == originals
    assert adopt_worker(retry.state_file).state == 'completed'
    assert source.state_file.with_name('calls').read_text() == 'run\n'


@pytest.mark.parametrize('kind', ['dirty', 'no-commit', 'accepted'])
def test_capture_with_ineligible_provenance_uses_normal_recovery(tmp_path, kind):
    from ai_skills.recovery import RecoveryEvidence, RecoveryPolicy, decide_recovery
    from test_dispatch import BUILD_CAPTURE, CAPTURE_CHECKS, WRITE_CAPTURE, capture_worker
    if kind == 'no-commit':
        body = "capture = Path(args[-2])\ncapture.write_bytes(b'not-json')\nresult_path.with_name('capture-original').write_bytes(b'not-json')\n"
    else:
        body = BUILD_CAPTURE + ("\nPath('feature.txt').write_text('dirty')\n" if kind == 'dirty' else '') + WRITE_CAPTURE
    source = capture_worker(tmp_path, body, result_capture=CAPTURE_CHECKS)
    result = dispatch_worker(source)
    saved = load_json(Path(str(source.state_file) + '.config.json'))
    repo = source.agent_run.repo_root
    head = git(repo, 'rev-parse', 'HEAD')
    capture = Path(saved['agent_run']['final_response_file'])
    assert capture.read_bytes() == source.agent_run.result_file.with_name('capture-original').read_bytes()
    expected = {'dirty': 'verification-dirty-worktree', 'no-commit': 'verification-source-commit-mismatch',
                'accepted': 'verification-source-not-terminal'}[kind]
    contract = {'source_state_file': str(source.state_file), 'source_attempt_id': saved['agent_run']['attempt_id'],
                'expected_commit_sha': head, 'required_checks': ['feature acceptance']}
    retry = attempt(tmp_path, repo, 'retry', VERIFY, cycle=4, execution_mode='verify_existing',
                    issue_base_sha=source.issue_base_sha, retry_contract=contract)
    with pytest.raises(ValueError, match=expected):
        dispatch_worker(retry)
    assert not retry.state_file.exists() and git(repo, 'rev-parse', 'HEAD') == head
    if kind == 'dirty': assert (repo / 'feature.txt').read_text() == 'dirty'
    worker = {'role': 'impl', 'cycle': 4, 'state_file': str(source.state_file)}
    evidence = RecoveryEvidence(issue='7', sub_state={'phase': 'impl'}, workers=((worker,
        {'state': result.state}, source.agent_run.done_file.exists(), source.agent_run.result_file.exists(),
        {'alive': False, 'reason': 'pids-dead'}),))
    decision = decide_recovery(evidence, RecoveryPolicy())
    reason = {'dirty': 'artifact-recovery-required', 'no-commit': 'in-flight-worker-failed',
              'accepted': 'in-flight-worker-finished'}[kind]
    assert (decision.action, decision.reason) == ('spawn_recovery', reason)
    assert decision.details['max_recovery_attempts'] == 2
    exhausted = decide_recovery(replace(evidence, recovery_attempts=2), RecoveryPolicy())
    assert (exhausted.action, exhausted.reason) == ('block', 'sub-coordinator-recovery-attempts-exhausted')


def test_captured_evidence_cannot_substitute_for_retry_evidence(tmp_path):
    source, result = rejected_capture(tmp_path, 'impl')
    assert result.state == 'artifact-recovery-required'
    saved = load_json(Path(str(source.state_file) + '.config.json'))
    repo = source.agent_run.repo_root
    head = git(repo, 'rev-parse', 'HEAD')
    contract = {'source_state_file': str(source.state_file), 'source_attempt_id': saved['agent_run']['attempt_id'],
                'expected_commit_sha': head, 'required_checks': ['feature acceptance']}
    verify = VERIFY.replace("Path('feature.txt').read_text()=='implemented\\n'",
                            "Path('feature.txt').read_text().startswith('implemented')")
    verify = verify.replace('payload_path.write_text(json.dumps(payload))',
                            "payload['verification'].pop('checks')\npayload_path.write_text(json.dumps(payload))")
    verify = verify.replace('sys.exit(main(', 'code=main(').replace("'--pre-spawn-head',head]))",
        "'--pre-spawn-head',head])\nassert code==1\nPath(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps(payload))")
    retry = attempt(tmp_path, repo, 'retry', verify, cycle=4, execution_mode='verify_existing',
                    issue_base_sha=source.issue_base_sha, retry_contract=contract)
    result = dispatch_worker(retry)
    assert (result.state, result.reason) == ('failed', 'verification-checks-incomplete')
    assert git(repo, 'rev-parse', 'HEAD') == head
