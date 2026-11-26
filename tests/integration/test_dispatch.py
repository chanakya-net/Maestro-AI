import json
import os
from pathlib import Path
import sys
import time

import pytest

from ai_skills.dispatch import WorkerConfig, dispatch_worker, inspect_worker, main as dispatch_main
from ai_skills.runner import AgentRunConfig
from dataclasses import replace


def worker_config(tmp_path, source, **overrides):
    script = tmp_path/'provider.py'
    script.write_text(source, encoding='utf-8')
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'agents':{'fake':{'invocation':{'command':sys.executable,'args_template':[str(script),'{{prompt}}']},'permission_modes':{'default':''}}}}))
    context = tmp_path/'context.md'; context.write_text('Context')
    prompt = tmp_path/'prompt.md'; prompt.write_text('Prompt')
    agent = AgentRunConfig(agent='fake',context_file=context,prompt_file=prompt,repo_root=tmp_path,
                          registry_file=registry,role='fixture',issue='7',gui_mode='0',environment=dict(os.environ),
                          log_file=tmp_path/'worker.log',done_file=tmp_path/'worker.done',result_file=tmp_path/'result.json',
                          events_log=tmp_path/'events',heartbeat_seconds=.03)
    return WorkerConfig(agent_run=agent,state_file=tmp_path/'worker.state.json',poll_seconds=.02,**overrides)


@pytest.mark.parametrize('hard_limit', [600.0, 0.0])
def test_bounded_attempt_receives_its_deadline(tmp_path, hard_limit):
    import time
    probe = tmp_path / 'deadline.json'
    config = worker_config(tmp_path, f"""import json, os
from pathlib import Path
Path({str(probe)!r}).write_text(json.dumps({{key: os.environ.get(key) for key in
    ('RUN_WITH_IT_ATTEMPT_DEADLINE_EPOCH', 'RUN_WITH_IT_ATTEMPT_DEADLINE', 'RUN_WITH_IT_ATTEMPT_HARD_LIMIT_SECONDS')}}))
""", hard_limit_seconds=hard_limit)
    started = time.time()
    dispatch_worker(config)
    seen = json.loads(probe.read_text())
    record = json.loads(Path(str(config.state_file) + '.config.json').read_text())
    assert 'RUN_WITH_IT_ATTEMPT_DEADLINE_EPOCH' not in json.dumps(record)
    if not hard_limit:
        assert seen == dict.fromkeys(seen)
        return
    assert started + hard_limit - 5 <= int(seen['RUN_WITH_IT_ATTEMPT_DEADLINE_EPOCH']) <= time.time() + hard_limit
    assert seen['RUN_WITH_IT_ATTEMPT_DEADLINE'].endswith('Z') and seen['RUN_WITH_IT_ATTEMPT_HARD_LIMIT_SECONDS'] == '600'


def test_admission_persists_pending_bound_outcome_before_provider(tmp_path):
    config=worker_config(tmp_path, '''import json,os
from pathlib import Path
state=Path(os.environ['RUN_WITH_IT_STATE_FILE'])
outcome=json.loads(Path(str(state)+'.outcome.json').read_text())
record=json.loads(Path(str(state)+'.config.json').read_text())
assert outcome['status']=='pending' and outcome['finished_at'] is None
assert outcome['run_id']==os.environ['RUN_WITH_IT_RUN_ID']
assert outcome['attempt_id']==os.environ['RUN_WITH_IT_ATTEMPT_ID']
assert outcome['expected_artifacts']=={'result':os.environ['RUN_WITH_IT_RESULT_FILE']}
assert record['agent_run']['dispatcher_managed'] is True
assert os.environ['RUN_WITH_IT_DISPATCHER_MANAGED']=='1'
Path('provider-invoked').touch()
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''')
    assert dispatch_worker(config).state=='completed'
    assert (tmp_path/'provider-invoked').exists()


@pytest.mark.parametrize('kind', ['result-state','result-config','result-outcome','result-prompt',
    'done-result','events-result','log-result','status-result','review-companion-done',
    'hardlink-input','ancestor-output','occupied-result','occupied-config','occupied-outcome','occupied-process'])
def test_admission_rejects_output_control_collisions_without_provider(tmp_path, kind):
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    run=config.agent_run
    aliases={'result-state':config.state_file,'result-config':Path(str(config.state_file)+'.config.json'),
             'result-outcome':Path(str(config.state_file)+'.outcome.json'),'result-prompt':run.prompt_file}
    if kind in aliases: run=run.replace(result_file=aliases[kind])
    elif kind=='done-result': run=run.replace(done_file=run.result_file)
    elif kind=='events-result': run=run.replace(events_log=run.result_file)
    elif kind=='log-result': run=run.replace(log_file=run.result_file)
    elif kind=='status-result': run=run.replace(status_file=run.result_file)
    elif kind=='review-companion-done':
        run=run.replace(role='review',result_file=tmp_path/'review-status.json',done_file=tmp_path/'review-instructions.json')
    elif kind=='hardlink-input':
        os.link(run.prompt_file,run.result_file)
    elif kind=='ancestor-output': run=run.replace(done_file=run.result_file/'done')
    else:
        target={'occupied-result':run.result_file,'occupied-config':Path(str(config.state_file)+'.config.json'),
                'occupied-outcome':Path(str(config.state_file)+'.outcome.json'),
                'occupied-process':Path(str(config.state_file)+'.process.json')}[kind]
        target.write_text('preserve me')
    config=replace(config,agent_run=run)
    with pytest.raises((ValueError,RuntimeError)):
        dispatch_worker(config)
    assert not (tmp_path/'provider-invoked').exists()


@pytest.mark.parametrize('role',['impl','modify','complexity','plan','review','artifact-recovery','merge-recovery'])
def test_admission_protects_issue_report_from_leaf_workers(tmp_path, role):
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    config=replace(config,agent_run=config.agent_run.replace(role=role,issue_dir=tmp_path,result_file=tmp_path/'report.json'))
    with pytest.raises(ValueError,match='report'):
        dispatch_worker(config)
    assert not (tmp_path/'provider-invoked').exists()


@pytest.mark.parametrize('suffix',['.config.json','.outcome.json'])
def test_admission_storage_failure_preserves_claim_without_provider(tmp_path, monkeypatch, suffix):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    original=dispatch.write_json_atomic
    def fail_write(path,value):
        if str(path)==str(config.state_file)+suffix: raise OSError('injected storage failure')
        original(path,value)
    monkeypatch.setattr(dispatch,'write_json_atomic',fail_write)
    with pytest.raises(OSError,match='injected storage failure'): dispatch_worker(config)
    assert not (tmp_path/'provider-invoked').exists()
    assert json.loads(config.state_file.read_text())['state']=='launching'
    with pytest.raises(RuntimeError): dispatch_worker(config)


def test_plan_success_requires_issue_directory_and_nonempty_markdown(tmp_path):
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    with pytest.raises(ValueError,match='issue_dir'):
        dispatch_worker(replace(config,agent_run=config.agent_run.replace(role='plan')))
    assert not (tmp_path/'provider-invoked').exists()
    plan={'schema_version':1,'issue':'7','role':'plan','status':'success','approach':'Build','complexity_level':'easy','complexity_rationale':'Single-file change along an existing seam.','slices':[{'id':1}]}
    config.agent_run.result_file.write_text(json.dumps(plan))
    (tmp_path/'plan.md').write_text('')
    snapshot=inspect_worker(replace(config,agent_run=config.agent_run.replace(role='plan',issue_dir=tmp_path)))
    assert snapshot.artifact_reason=='missing-plan-file-artifact'


@pytest.mark.parametrize('variant', ['stock', 'alias', 'forged'])
def test_managed_agy_plan_rejects_unverified_profile(tmp_path, monkeypatch, variant):
    from ai_skills import dispatch, runner

    config = worker_config(tmp_path, '')
    run = config.agent_run.replace(agent='agy-cli' if variant == 'alias' else 'agy',
        registry_file=None, role='plan', issue_dir=tmp_path, dispatcher_managed=False)
    if variant == 'forged':
        registry = runner.load_registry()
        registry['agents']['agy']['permission_modes']['read_only'] = '--pretend-read-only'
        run = run.replace(registry_file=config.agent_run.registry_file, permission_mode='safe',
            unattended=True, environment={**os.environ, 'AGY_READ_ONLY_VERIFIED': '1'})
        run.registry_file.write_text(json.dumps(registry), encoding='utf-8')

    def unexpected(*args, **kwargs):
        pytest.fail('unverified AGY must be refused before invocation or provider start')

    monkeypatch.setattr(dispatch, 'build_invocation', unexpected)
    monkeypatch.setattr(runner, 'build_invocation', unexpected)
    monkeypatch.setattr(runner.ProcessSupervisor, 'start', unexpected)
    with pytest.raises(ValueError, match='agy-plan-read-only-unavailable'):
        dispatch_worker(replace(config, agent_run=run))
    with pytest.raises(ValueError, match='agy-plan-read-only-unavailable'):
        runner.run_agent(run.replace(dispatcher_managed=True))
    assert not config.state_file.exists()


def test_review_aliases_are_authoritatively_bound_before_provider(tmp_path):
    config=worker_config(tmp_path,'''import os,json
from pathlib import Path
Path('provider-aliases.json').write_text(json.dumps([os.environ['REVIEWER_STATUS_FILE'],os.environ['REVIEWER_INSTRUCTIONS_FILE']]))
''')
    config=replace(config,agent_run=config.agent_run.replace(role='review',result_file=tmp_path/'review-status.json',
        environment={**os.environ,'REVIEWER_STATUS_FILE':'foreign','REVIEWER_INSTRUCTIONS_FILE':'foreign'}))
    dispatch_worker(config)
    assert json.loads((tmp_path/'provider-aliases.json').read_text())==[str(tmp_path/'review-status.json'),str(tmp_path/'review-instructions.json')]


def test_dispatch_binds_relative_output_paths_to_absolute_environment(tmp_path,monkeypatch):
    config=worker_config(tmp_path,'''import json,os
from pathlib import Path
result=Path(os.environ['RUN_WITH_IT_RESULT_FILE'])
assert result.is_absolute()
Path('provider-invoked').touch()
result.write_text('{}')
''')
    monkeypatch.chdir(tmp_path)
    config=replace(config,agent_run=config.agent_run.replace(result_file=Path('result.json'),done_file=Path('worker.done')))
    assert dispatch_worker(config).state=='completed'
    assert (tmp_path/'provider-invoked').exists()


@pytest.mark.parametrize('field',['process_state_file','stream_directory','record_directory','tail_state_file'])
def test_admission_rejects_runtime_file_or_directory_alias_to_input(tmp_path,field):
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    if field=='tail_state_file': config=replace(config,tail_state_file=config.agent_run.prompt_file)
    else: config=replace(config,agent_run=config.agent_run.replace(**{field:config.agent_run.prompt_file}))
    with pytest.raises(ValueError,match='collision'): dispatch_worker(config)
    assert not (tmp_path/'provider-invoked').exists()


@pytest.mark.parametrize('field',['stream_directory','record_directory'])
@pytest.mark.parametrize('nested',[False,True])
def test_admission_rejects_runtime_directories_in_state_lock_without_provider(tmp_path,field,nested):
    from ai_skills.storage import DirectoryLock
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()")
    lock=Path(str(config.state_file)+'.lock')
    directory=lock/'runtime' if nested else lock
    config=replace(config,agent_run=config.agent_run.replace(**{field:directory}))
    with pytest.raises(ValueError,match='collision'):
        dispatch_worker(config)
    assert not (tmp_path/'provider-invoked').exists()
    assert not config.state_file.exists()
    with DirectoryLock(config.state_file,timeout_seconds=.1):
        pass
    assert not lock.exists()


def test_admission_allows_streams_nested_in_record_directory(tmp_path):
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    records=tmp_path/'records'
    config=replace(config,agent_run=config.agent_run.replace(record_directory=records,stream_directory=records/'streams'))
    assert dispatch_worker(config).state=='completed'


@pytest.mark.parametrize('dispatcher_claim',[False,True])
def test_admission_rejects_existing_supervisor_claim_before_wrapper(tmp_path,dispatcher_claim):
    from ai_skills.processes import ProcessSupervisor
    config=worker_config(tmp_path,"from pathlib import Path;Path('provider-invoked').touch()",detach=True)
    record_directory=tmp_path/'records'
    config=replace(config,agent_run=config.agent_run.replace(record_directory=record_directory,run_id='run-7',attempt_id='attempt-1'))
    supervisor=ProcessSupervisor(record_directory)
    claimed_run='run-7-dispatcher' if dispatcher_claim else 'run-7'
    claim=supervisor._path(claimed_run,'attempt-1')
    claim.write_text(json.dumps({'schema_version':1,'run_id':claimed_run,'attempt_id':'attempt-1','state':'starting'}))
    with pytest.raises(RuntimeError,match='claim'): dispatch_worker(config)
    assert not config.state_file.exists()
    assert not (tmp_path/'provider-invoked').exists()


def test_done_hint_does_not_complete_worker_before_provider_exit(tmp_path):
    config = worker_config(tmp_path, '''import os,time,json
from pathlib import Path
Path(os.environ['RUN_WITH_IT_DONE_FILE']).write_text('DONE|status=success')
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
time.sleep(.2)
Path('cleanup-complete').touch()
''')
    started = time.monotonic()
    result = dispatch_worker(config)
    assert time.monotonic()-started >= .2
    assert result.state == 'completed'
    assert result.return_code == 0
    assert (tmp_path/'cleanup-complete').exists()
    snapshot = inspect_worker(config)
    assert snapshot.liveness == 'dead'
    assert snapshot.artifact_valid and snapshot.done_present


def test_clean_exit_without_artifact_is_not_success(tmp_path):
    config = worker_config(tmp_path, 'print("provider exited cleanly")')
    result = dispatch_worker(config)
    assert result.state == 'failed'
    assert result.return_code != 0
    assert result.reason == 'missing-result-artifact'


def test_quiet_but_live_is_not_failed_at_stall_interval(tmp_path):
    config = worker_config(tmp_path, '''import os,time
from pathlib import Path
time.sleep(.25)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''',quiet_seconds=.01,stall_seconds=.05,hard_limit_seconds=2)
    assert dispatch_worker(config).state == 'completed'


def test_hard_deadline_reaps_owned_provider(tmp_path):
    config = worker_config(tmp_path, 'import time; time.sleep(20)',hard_limit_seconds=.15)
    result = dispatch_worker(config)
    assert result.return_code == 124
    assert result.reason == 'hard-limit-exceeded'
    assert inspect_worker(config).liveness == 'dead'


def test_detach_returns_only_after_durable_provider_or_failure(tmp_path):
    config = worker_config(tmp_path, '''import os,time
from pathlib import Path
time.sleep(.4)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''',detach=True)
    result = dispatch_worker(config)
    state = json.loads(config.state_file.read_text())
    assert result.state in ('running','completed')
    assert state.get('provider_identity')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and json.loads(config.state_file.read_text())['state']=='running':
        time.sleep(.03)
    assert json.loads(config.state_file.read_text())['state']=='completed'


def test_from_sources_cli_over_environment_and_forwarded_fields(tmp_path):
    config = WorkerConfig.from_sources({'agent':'cli','model':'cli model','role':'review','issue':'8','state_file':str(tmp_path/'state'),
                                        'context_file':str(tmp_path/'context'),'prompt_file':str(tmp_path/'prompt'),
                                        'repo_root':str(tmp_path)},
                                       {'AGENT':'env','MODEL':'env model','MODEL_EFFORT':'xhigh','SUB_COORD_AGENT':'other',
                                        'RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS':'42','RUN_WITH_IT_EVENTS_LOG':str(tmp_path/'events')})
    assert config.agent_run.agent == 'cli'
    assert config.agent_run.model == 'cli model'
    assert config.agent_run.effort == 'xhigh'
    assert config.agent_run.events_log == str(tmp_path/'events')
    assert config.hard_limit_seconds == 42


@pytest.mark.parametrize(('role', 'hard_limit'), [('impl', 21600), ('sub-coord', 0), ('merge-recovery', 0)])
def test_role_defaults_and_explicit_recorded_deadlines(tmp_path, role, hard_limit):
    config = worker_config(tmp_path, 'from pathlib import Path;Path("result.json").write_text("{}")')
    config = replace(config, agent_run=config.agent_run.replace(role=role), hard_limit_seconds=None)
    assert config.timeout_seconds == 0
    assert config.hard_limit_seconds == hard_limit
    assert (config.quiet_seconds, config.stall_seconds) == (900, 1200)

    restored = WorkerConfig.from_record({**config.to_record(), 'timeout_seconds': 3600, 'hard_limit_seconds': 7200})
    assert restored.timeout_seconds == 3600
    assert restored.hard_limit_seconds == 7200


def test_persisted_explicit_deadline_record_is_enforced_after_reload(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ai_skills import dispatch
    from pool_support import SimulatedClock

    config = worker_config(tmp_path, 'import time; time.sleep(5)')
    config = replace(config, agent_run=config.agent_run.replace(role='sub-coord'), hard_limit_seconds=None)
    persisted = {**config.to_record(), 'timeout_seconds': 3600, 'hard_limit_seconds': 7200}
    reloaded = WorkerConfig.from_record(persisted)
    clock = SimulatedClock(600, reloaded.state_file)
    monkeypatch.setattr(dispatch, 'time', SimpleNamespace(monotonic=clock.monotonic, sleep=time.sleep, time=time.time))
    result = dispatch_worker(reloaded)
    assert result.reason == 'dispatch-timeout'
    assert result.return_code == 124
    assert inspect_worker(reloaded).liveness == 'dead'


@pytest.mark.parametrize('role', ['sub-coord', 'merge-recovery'])
def test_coordinator_dispatch_survives_both_former_deadlines(tmp_path, monkeypatch, role):
    from types import SimpleNamespace
    from ai_skills import dispatch

    release = tmp_path / 'release'
    source = (
        'import json, os, time\n'
        'from pathlib import Path\n'
        f'release = Path({str(release)!r})\n'
        'end = time.monotonic() + 10\n'
        'while not release.exists() and time.monotonic() < end:\n'
        '    time.sleep(.02)\n'
        "Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(\n"
        "    json.dumps({'outcome': 'completed', 'summary': 'fixture complete'}), encoding='utf-8')\n"
    )
    config = worker_config(tmp_path, source)
    config = replace(config, agent_run=config.agent_run.replace(role=role), hard_limit_seconds=None)
    real_time = time
    elapsed = [0]

    def simulated_monotonic():
        elapsed[0] += 600
        if elapsed[0] > 7200 and not release.exists():
            release.write_text('go', encoding='utf-8')
        return elapsed[0]

    monkeypatch.setattr(dispatch, 'time', SimpleNamespace(
        monotonic=simulated_monotonic, sleep=real_time.sleep, time=real_time.time))
    assert dispatch.dispatch_worker(config).state == 'completed'
    assert elapsed[0] > 7200


@pytest.mark.parametrize(('limit', 'expected'), [('timeout_seconds', 3600), ('hard_limit_seconds', 7200)])
def test_explicit_coordinator_deadline_still_stops_owned_provider(tmp_path, monkeypatch, limit, expected):
    from types import SimpleNamespace
    from ai_skills import dispatch

    source = "import os,time,json; from pathlib import Path; time.sleep(2); Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps({'outcome':'completed','summary':'fixture complete'}))"
    config = worker_config(tmp_path, source)
    config = replace(config, agent_run=config.agent_run.replace(role='sub-coord'), hard_limit_seconds=None)
    config = replace(config, **{limit: expected})
    real_time = time
    elapsed = [0]

    def simulated_monotonic():
        elapsed[0] += 600
        return elapsed[0]

    monkeypatch.setattr(dispatch, 'time', SimpleNamespace(
        monotonic=simulated_monotonic, sleep=real_time.sleep, time=real_time.time))
    result = dispatch.dispatch_worker(config)
    assert result.reason == ('dispatch-timeout' if limit == 'timeout_seconds' else 'hard-limit-exceeded')
    assert elapsed[0] >= expected


def test_wrapper_exit_waits_for_descendant_cleanup(tmp_path):
    config=worker_config(tmp_path, '''import os,subprocess,sys
subprocess.Popen([sys.executable,'-c',"import os,time;from pathlib import Path;time.sleep(.2);Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}');Path('child-cleanup').touch()"])
''')
    result=dispatch_worker(config)
    assert result.state=='completed'
    assert (tmp_path/'child-cleanup').exists()
    assert inspect_worker(config).liveness=='dead'


def test_detach_missing_provider_records_failure_instead_of_running(tmp_path):
    config=worker_config(tmp_path,'',detach=True)
    registry=json.loads(config.agent_run.registry_file.read_text())
    registry['agents']['fake']['invocation']['command']='ai-skills-missing-detached-provider'
    config.agent_run.registry_file.write_text(json.dumps(registry))
    result=dispatch_worker(config)
    assert result.state=='failed'
    assert result.return_code==127
    assert json.loads(config.state_file.read_text())['state']=='failed'


def test_cli_json_config_can_execute_without_shell_wrapper(tmp_path):
    from ai_skills.dispatch import main
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    path=tmp_path/'config.json';path.write_text(json.dumps(config.to_record()))
    assert main(['dispatch','--config',str(path)])==0
    assert main(['inspect','--config',str(path)])==0


def test_quota_after_commit_keeps_harness_exclusion_through_artifact_recovery(tmp_path):
    """918 regression: work committed, then the Claude 5-hour limit ended the session."""
    import subprocess
    from dataclasses import replace
    from ai_skills import routing
    def git(*args):
        return subprocess.check_output(['git','-C',str(tmp_path),*args],stderr=subprocess.DEVNULL,text=True).strip()
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (tmp_path/'initial').write_text('initial');git('add','initial');git('commit','-m','initial')
    pre=git('rev-parse','HEAD')
    config=worker_config(tmp_path, """import subprocess, sys
from pathlib import Path
Path('committed-work').write_text('implemented')
subprocess.check_call(['git','add','committed-work'])
subprocess.check_call(['git','commit','-m','implementation'])
print("You've hit your 5-hour limit \u00b7 resets 3:40am (Asia/Calcutta)", file=sys.stderr)
sys.exit(1)
""")
    config=replace(config,agent_run=config.agent_run.replace(role='impl'),pre_spawn_head=pre)
    result=dispatch_worker(config)
    assert result.state=='artifact-recovery-required'
    state=json.loads(config.state_file.read_text())
    assert (state['failure_class'], state['reason'], state['unavailable_reason']) == (
        'infrastructure', 'artifact-recovery-required', 'quota')
    assert 0 < state['unavailable_until'] - state['completed_at'] <= 24 * 3600
    routes = routing.failed_attempt_routes([config.state_file], now=state['completed_at'])
    assert [reason for _agent, _model, reason in routes] == ['quota']
    assert routing.failed_attempt_routes([config.state_file], now=state['unavailable_until'] + 1) == []


def test_commit_without_artifact_requires_recovery_not_verified_success(tmp_path):
    import subprocess
    from dataclasses import replace
    def git(*args):
        return subprocess.check_output(['git','-C',str(tmp_path),*args],stderr=subprocess.DEVNULL,text=True).strip()
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (tmp_path/'initial').write_text('initial');git('add','initial');git('commit','-m','initial')
    pre=git('rev-parse','HEAD')
    config=worker_config(tmp_path, '''import subprocess
from pathlib import Path
Path('committed-work').write_text('implemented')
subprocess.check_call(['git','add','committed-work'])
subprocess.check_call(['git','commit','-m','implementation'])
''')
    config=replace(config,agent_run=config.agent_run.replace(role='impl'),pre_spawn_head=pre)
    result=dispatch_worker(config)
    assert result.state=='artifact-recovery-required'
    assert result.return_code==75
    payload=json.loads(config.agent_run.result_file.read_text())
    assert payload['verification']['passed'] is False
    assert payload['status']=='artifact_recovery_required'


@pytest.mark.parametrize('repetition',range(5))
def test_adopt_dead_dispatcher_never_relaunches_paid_attempt(tmp_path,repetition):
    import psutil
    from ai_skills.dispatch import adopt_worker
    from ai_skills.processes import ProcessIdentity,ProcessSupervisor
    config=worker_config(tmp_path, '''import os,time
from pathlib import Path
with Path('launch-count').open('a') as stream: stream.write('one\\n')
Path('provider-ready').touch()
while not Path('release-provider').exists(): time.sleep(.01)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''',detach=True)
    process=None
    try:
        result=dispatch_worker(config)
        deadline=time.monotonic()+15
        while not (tmp_path/'provider-ready').exists():
            assert time.monotonic()<deadline, 'provider did not acknowledge startup'
            time.sleep(.01)
        wrapper=json.loads(Path(str(config.state_file)+'.dispatcher.process.json').read_text())
        process=psutil.Process(wrapper['identity']['pid'])
        assert process.create_time()==wrapper['identity']['created_at']
        process.kill()
        process.wait(timeout=5)
        # The provider's explicit release gate proves work remains in flight;
        # startup and kill timing cannot accidentally test an already-dead run.
        assert adopt_worker(config.state_file).state=='running'
        (tmp_path/'release-provider').touch()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            result=adopt_worker(config.state_file)
            if result.state!='running': break
            time.sleep(.03)
        assert result.state=='completed', result.reason
        assert (tmp_path/'launch-count').read_text().splitlines()==['one']
    finally:
        (tmp_path/'release-provider').touch()
        provider_path=Path(str(config.state_file)+'.process.json')
        if provider_path.exists():
            record=json.loads(provider_path.read_text())
            ProcessSupervisor(Path(record['record_directory'])).stop(ProcessIdentity(**record['identity']),grace_seconds=.05)
        if process is not None and process.is_running():
            try: process.kill();process.wait(timeout=5)
            except psutil.NoSuchProcess: pass


def test_nested_worker_selection_remains_pinned_against_ambient_forced_env(tmp_path):
    config=worker_config(tmp_path,'')
    selected=WorkerConfig.from_sources({'agent_run':config.agent_run,'state_file':str(config.state_file)},
                                       {'FORCED_AGENT':'ambient-other','FORCED_MODEL':'ambient-model'})
    assert selected.agent_run.agent=='fake'
    assert selected.agent_run.model==config.agent_run.model


def test_cli_overrides_nested_json_configuration(tmp_path,capsys):
    from ai_skills.dispatch import main
    config=worker_config(tmp_path,'')
    registry=json.loads(config.agent_run.registry_file.read_text())
    registry['agents']['fake']['model']={'flag_template':'--model {{model}}'}
    registry['agents']['fake']['invocation']['args_template']=['{{model_flag}}','{{prompt}}']
    config.agent_run.registry_file.write_text(json.dumps(registry))
    path=tmp_path/'config.json';path.write_text(json.dumps(config.to_record()))
    assert main(['dispatch','--config',str(path),'--model','CLI model','--dry-run'])==0
    assert 'CLI model' in capsys.readouterr().out


def test_inspection_preserves_worker_diagnostic_fields_and_tail_cursor(tmp_path,capsys):
    from dataclasses import replace
    from ai_skills.dispatch import main
    config=worker_config(tmp_path,"import os;from pathlib import Path;print('activity');Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    config=replace(config,tail_state_file=tmp_path/'tail.json')
    assert dispatch_worker(config).state=='completed'
    snapshot=inspect_worker(config)
    assert snapshot.log_tail_changed is True
    assert not config.tail_state_file.exists()
    config.tail_state_file.write_text(json.dumps({'schema_version':1,'tail_hash':snapshot.tail_hash}))
    assert inspect_worker(config).log_tail_changed is False
    assert main(['inspect','--state-file',str(config.state_file)])==0
    output=capsys.readouterr().out
    assert 'WORKER|pid=' in output
    assert '|alive=false|done=true|log_present=true|log_tail_changed=false|tail_hash=' in output


def test_empty_git_repository_does_not_crash_worker_admission(tmp_path):
    import subprocess
    from dataclasses import replace
    subprocess.run(['git','init',str(tmp_path)],check=True,capture_output=True)
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('[]')")
    config=replace(config,agent_run=config.agent_run.replace(role='impl'))
    result=dispatch_worker(config)
    assert result.state=='failed'
    assert result.reason=='invalid-result-artifact'


def test_dispatch_scrubs_ambient_aliases_and_preserves_independent_canonical_selectors(tmp_path,monkeypatch):
    from dataclasses import replace
    config=worker_config(tmp_path,'''import json,os
from pathlib import Path
keys=('AGENT','MODEL','FORCED_AGENT','SUB_COORD_AGENT','AI_SKILLS_RUN_ROOT','RUN_WITH_IT_CYCLE')
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps({key:os.environ.get(key) for key in keys}))
''')
    monkeypatch.setenv('AGENT','global-ambient-agent')
    monkeypatch.setenv('MODEL','global-ambient-model')
    environment={**os.environ,'AGENT':'ambient-agent','MODEL':'ambient-model','FORCED_AGENT':'worker-selected',
                 'SUB_COORD_AGENT':'coordinator-selected','AI_SKILLS_RUN_ROOT':str(tmp_path.parent)}
    config=replace(config,agent_run=config.agent_run.replace(environment=environment,cycle='2'))
    assert dispatch_worker(config).state=='completed'
    actual=json.loads(config.agent_run.result_file.read_text())
    assert actual['AGENT'] is None and actual['MODEL'] is None
    assert actual['FORCED_AGENT']=='worker-selected'
    assert actual['SUB_COORD_AGENT']=='coordinator-selected'
    assert actual['AI_SKILLS_RUN_ROOT']==str(tmp_path.parent)
    assert actual['RUN_WITH_IT_CYCLE']=='2'


@pytest.mark.parametrize(('source','status','state','code','reason'), [
    ('pass','failed','failed',1,'missing-result-artifact'),
    ("import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')",'success','completed',0,''),
    ("import sys;print('usage limit exceeded',file=sys.stderr);sys.exit(7)",'failed','failed',7,'quota'),
])
def test_runtime_outcome_owns_terminal_done_and_events(tmp_path,source,status,state,code,reason):
    from ai_skills.dispatch import outcome_path
    config=worker_config(tmp_path,source)
    result=dispatch_worker(config)
    outcome=json.loads(outcome_path(config.state_file).read_text())
    assert (result.state,result.return_code,result.reason)==(state,code,reason)
    assert (outcome['status'],outcome['worker_return_code'],outcome['reason'])==(status,code,reason)
    assert outcome['provider_return_code']==(7 if code==7 else 0)
    assert outcome['finished_at']>=outcome['started_at']
    assert f'status={status}' in config.agent_run.done_file.read_text()
    assert 'source=dispatcher' in config.agent_run.done_file.read_text()
    events=config.agent_run.events_log.read_text()
    assert 'type=provider-exit|' in events
    assert 'type=worker-done|' not in events
    assert outcome['failure_class']==('infrastructure' if code==7 else 'capability')


def test_managed_complexity_synthesizes_valid_stdout_json_on_clean_exit(tmp_path):
    payload = {
        'schema_version': 2,
        'total': 18,
        'level': 'medium',
        'scores': {
            'coupling_and_blast_radius': 3,
            'pattern_novelty': 2,
            'state_and_concurrency_risk': 3,
            'data_and_rollback_risk': 2,
            'verification_risk': 3,
            'ambiguity_of_requirements': 2,
            'scope_size': 3,
        },
        'rationale': {
            'coupling_and_blast_radius': 'Bounded module.',
            'pattern_novelty': 'Existing seam.',
            'state_and_concurrency_risk': 'Standard lock.',
            'data_and_rollback_risk': 'Additive only.',
            'verification_risk': 'Existing harness.',
            'ambiguity_of_requirements': 'Clear criteria.',
            'scope_size': 'Moderate slice.',
        },
        'files_inspected': ['src/ai_skills/runner.py'],
    }
    source = (
        f"import json; print('COMPLEXITY|score=18|level=medium|d1=3|d2=2|d3=3|d4=2|d5=3|d6=2|d7=3'); "
        f"print('```json'); print(json.dumps({payload!r})); print('```')"
    )
    config = worker_config(tmp_path, source)
    config = replace(config, agent_run=config.agent_run.replace(role='complexity'))
    result = dispatch_worker(config)
    assert (result.state, result.return_code, result.reason) == ('completed', 0, '')
    saved = json.loads(config.agent_run.result_file.read_text(encoding='utf-8'))
    assert saved['total'] == 18 and saved['level'] == 'medium'
    assert saved['source'] == 'dispatcher-synthesized-from-log'


def test_managed_complexity_never_synthesizes_from_nonzero_exit(tmp_path):
    payload = {
        'schema_version': 2,
        'total': 18,
        'level': 'medium',
        'scores': {
            'coupling_and_blast_radius': 3,
            'pattern_novelty': 2,
            'state_and_concurrency_risk': 3,
            'data_and_rollback_risk': 2,
            'verification_risk': 3,
            'ambiguity_of_requirements': 2,
            'scope_size': 3,
        },
        'rationale': {k: 'reason' for k in (
            'coupling_and_blast_radius', 'pattern_novelty', 'state_and_concurrency_risk',
            'data_and_rollback_risk', 'verification_risk', 'ambiguity_of_requirements', 'scope_size',
        )},
        'files_inspected': ['src/ai_skills/runner.py'],
    }
    source = f"import json, sys; print(json.dumps({payload!r})); sys.exit(1)"
    config = worker_config(tmp_path, source)
    config = replace(config, agent_run=config.agent_run.replace(role='complexity'))
    result = dispatch_worker(config)
    assert result.state == 'failed' and result.return_code == 1
    assert not config.agent_run.result_file.exists()


@pytest.mark.parametrize(('message', 'expected_reason'), [
    ("You've hit your 5-hour limit · resets 8:50pm (Asia/Calcutta)", 'quota'),
    ('ERROR: This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing your request.', 'policy-refusal'),
])
def test_managed_quota_and_policy_refusal_exclude_harness(tmp_path, message, expected_reason):
    from ai_skills import routing
    source = f"import sys; print({message!r}, file=sys.stderr); sys.exit(1)"
    config = worker_config(tmp_path, source)
    result = dispatch_worker(config)
    state = json.loads(config.state_file.read_text(encoding='utf-8'))
    assert (result.state, result.reason) == ('failed', expected_reason)
    assert (state['failure_class'], state['reason']) == ('infrastructure', expected_reason)
    availability = routing.availability_from_failed_attempts([config.state_file])
    assert config.agent_run.agent in availability['agents']


@pytest.mark.parametrize('status',['failed','blocked','interrupted'])
def test_explicit_role_failure_is_terminal_without_companions_or_synthesis(tmp_path,monkeypatch,status):
    from ai_skills import artifacts
    source='''import os,json
from pathlib import Path
payload={'schema_version':1,'artifact_kind':'role_failure','run_id':os.environ['RUN_WITH_IT_RUN_ID'],'attempt_id':os.environ['RUN_WITH_IT_ATTEMPT_ID'],'issue':'7','role':'plan','status':STATUS,'reason':'cannot-proceed','summary':'Required input unavailable'}
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps(payload))
'''.replace('STATUS',repr(status))
    config=worker_config(tmp_path,source)
    config=replace(config,agent_run=config.agent_run.replace(role='plan',issue_dir=tmp_path))
    for name in ('synthesize_implementation','synthesize_review','synthesize_complexity'):
        monkeypatch.setattr(artifacts,name,lambda args:pytest.fail('failure report reached synthesis'))
    result=dispatch_worker(config)
    outcome=json.loads(Path(str(config.state_file)+'.outcome.json').read_text())
    assert outcome['status']==status
    assert outcome['reason']=='cannot-proceed'
    assert result.return_code==(130 if status=='interrupted' else 1)
    assert result.state==('interrupted' if status=='interrupted' else 'failed')
    assert not (tmp_path/'plan.md').exists()
    assert json.loads(config.agent_run.result_file.read_text())['artifact_kind']=='role_failure'


def test_malformed_provider_bytes_never_reach_synthesis(tmp_path,monkeypatch):
    from ai_skills import artifacts
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{broken')")
    config=replace(config,agent_run=config.agent_run.replace(role='impl'))
    for name in ('synthesize_implementation','synthesize_review','synthesize_complexity'):
        monkeypatch.setattr(artifacts,name,lambda args:pytest.fail('malformed provider evidence reached synthesis'))
    result=dispatch_worker(config)
    assert (result.state,result.reason)==('failed','invalid-result-artifact')
    assert config.agent_run.result_file.read_bytes()==b'{broken'


def test_forged_success_done_is_replaced_after_missing_artifact(tmp_path):
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_DONE_FILE']).write_text('DONE|status=success|source=provider')")
    assert dispatch_worker(config).state=='failed'
    done=config.agent_run.done_file.read_text()
    assert 'status=failed' in done and 'status=success' not in done
    assert 'source=dispatcher' in done


@pytest.mark.parametrize('phase',['outcome','state','done'])
def test_terminal_storage_failure_keeps_claim_and_adoption_repairs_journal(tmp_path,monkeypatch,phase):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    real_json,real_text=dispatch.write_json_atomic,getattr(dispatch,'write_text_atomic',None)
    seen=[]
    def write_json(path,value):
        terminal=value.get('status') in ('success','failed') if str(path).endswith('.outcome.json') else value.get('state')=='completed'
        if terminal:
            kind='outcome' if str(path).endswith('.outcome.json') else 'state'
            seen.append(kind)
            if kind==phase:raise OSError('injected terminal '+phase)
        return real_json(path,value)
    def write_text(path,value):
        seen.append('done')
        if phase=='done':raise OSError('injected terminal done')
        return real_text(path,value)
    monkeypatch.setattr(dispatch,'write_json_atomic',write_json)
    monkeypatch.setattr(dispatch,'write_text_atomic',write_text,raising=False)
    with pytest.raises(OSError,match='injected terminal'):dispatch_worker(config)
    assert config.state_file.exists()
    assert config.agent_run.result_file.read_bytes()==b'{}'
    assert 'type=dispatch-complete|' not in config.agent_run.events_log.read_text()
    with pytest.raises(RuntimeError,match='prohibits relaunch'):dispatch_worker(config)
    monkeypatch.setattr(dispatch,'write_json_atomic',real_json)
    if real_text:monkeypatch.setattr(dispatch,'write_text_atomic',real_text)
    if phase=='outcome':
        state=json.loads(config.state_file.read_text())
        state['launcher_identity']['created_at']+=1
        real_json(config.state_file,state)
    assert dispatch.adopt_worker(config.state_file).state=='completed'
    assert 'source=dispatcher' in config.agent_run.done_file.read_text()
    assert seen==(['outcome'] if phase=='outcome' else ['outcome','state'] if phase=='state' else ['outcome','state','done'])


@pytest.mark.parametrize('corrupt',['{broken','null','binding','terminal-conflict'])
def test_adoption_rejects_corrupt_stale_or_conflicting_journal(tmp_path,corrupt):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    dispatch_worker(config)
    path=dispatch.outcome_path(config.state_file)
    payload=json.loads(path.read_text())
    if corrupt=='binding':payload['attempt_id']='foreign'
    elif corrupt=='terminal-conflict':payload.update(status='failed',worker_return_code=9,reason='fabricated')
    if corrupt=='null':
        state=json.loads(config.state_file.read_text())
        state['launcher_identity']['created_at']+=1
        config.state_file.write_text(json.dumps(state))
    path.write_text(corrupt if corrupt in ('{broken','null') else json.dumps(payload))
    before=path.read_bytes()
    result=dispatch.adopt_worker(config.state_file)
    assert result.state=='unknown' and result.return_code==3
    assert path.read_bytes()==before


def test_adoption_terminal_state_does_not_bypass_missing_exit(tmp_path,monkeypatch):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    dispatch_worker(config)
    monkeypatch.setattr(dispatch.ProcessSupervisor,'return_code',lambda self,identity:None)
    assert dispatch.adopt_worker(config.state_file).state=='unknown'


def test_inspection_terminal_state_without_identity_is_unknown_and_readonly(tmp_path):
    config=worker_config(tmp_path,'pass')
    config.state_file.write_text('{"state":"completed"}')
    assert inspect_worker(config).liveness=='unknown'


def test_legacy_completed_attempt_revalidates_artifact_after_death(tmp_path):
    from ai_skills import dispatch
    config=worker_config(tmp_path,'pass')
    dispatch_worker(config)
    dispatch.outcome_path(config.state_file).unlink()
    state=json.loads(config.state_file.read_text());state.update(state='completed',return_code=0)
    state['launcher_identity']['created_at']+=1
    config.state_file.write_text(json.dumps(state))
    record=json.loads(Path(str(config.state_file)+'.config.json').read_text())
    record['agent_run'].pop('dispatcher_managed')
    Path(str(config.state_file)+'.config.json').write_text(json.dumps(record))
    result=dispatch.adopt_worker(config.state_file)
    assert (result.state,result.reason)==('failed','missing-result-artifact')
    assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='failed'


def test_inspection_never_persists_tail_or_terminal_decisions(tmp_path):
    config=worker_config(tmp_path,"import os;from pathlib import Path;print('activity');Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    config=replace(config,tail_state_file=tmp_path/'tail.json')
    dispatch_worker(config)
    config.tail_state_file.write_text('{"tail_hash":"previous"}')
    paths=[config.state_file,Path(str(config.state_file)+'.outcome.json'),config.agent_run.result_file,config.agent_run.done_file,config.tail_state_file]
    before={path:path.read_bytes() for path in paths}
    assert inspect_worker(config).log_tail_changed
    assert inspect_worker(config).log_tail_changed
    assert {path:path.read_bytes() for path in paths}==before


def test_live_launcher_gap_cannot_be_adopted_as_bootstrap_failure(tmp_path,monkeypatch):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    real_work=dispatch._work
    def work(selected):
        result=dispatch.adopt_worker(selected.state_file)
        assert result.state in ('running','unknown')
        assert json.loads(dispatch.outcome_path(selected.state_file).read_text())['status']=='pending'
        assert not selected.agent_run.done_file.exists()
        return real_work(selected)
    monkeypatch.setattr(dispatch,'_work',work)
    assert dispatch_worker(config).state=='completed'


def test_detached_terminal_state_cannot_return_success_before_done_publication(tmp_path,monkeypatch):
    from ai_skills import dispatch
    from ai_skills.processes import ProcessSupervisor
    config=worker_config(tmp_path,'pass',detach=True)
    original_start=ProcessSupervisor.start
    def start(supervisor,spec,*,run_id,attempt_id):
        if run_id.endswith('-dispatcher'):
            bootstrap='''import sys,time
sys.path.insert(0,sys.argv[1])
from ai_skills import dispatch
from ai_skills.storage import load_json
from pathlib import Path
config=dispatch.WorkerConfig.from_record(load_json(Path(sys.argv[2])))
# No running-state observation: publish a complete provider attempt straight
# into terminal outcome/state, then fail DONE on this wrapper and adopter.
run=config.agent_run
Path(run.result_file).write_text('{}')
Path(run.done_file).mkdir()
def invoke(run):
    from ai_skills.runner import run_agent
    return run_agent(run)
# Real provider writes no outputs; the valid fixture artifact is already here.
real_text=dispatch.write_text_atomic
def fail(path,text):
    time.sleep(.15)
    raise OSError('injected DONE publication failure')
dispatch.write_text_atomic=fail
real_json=dispatch.write_json_atomic
def publish(path,value):
    if path==config.state_file and value.get('state') in ('starting','running','quiet','stalled'):
        return
    real_json(path,value)
dispatch.write_json_atomic=publish
raise SystemExit(dispatch._work(config).return_code)
'''
            spec=replace(spec,argv=(*spec.argv[:2],bootstrap,*spec.argv[3:]))
        return original_start(supervisor,spec,run_id=run_id,attempt_id=attempt_id)
    monkeypatch.setattr(ProcessSupervisor,'start',start)
    with pytest.raises(OSError):dispatch_worker(config)
    assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='success'
    assert 'type=dispatch-complete|' not in config.agent_run.events_log.read_text()


def test_adoption_rejects_config_bound_to_another_state_file(tmp_path):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    dispatch_worker(config)
    saved=Path(str(config.state_file)+'.config.json')
    record=json.loads(saved.read_text());record['state_file']=str(tmp_path/'foreign.state')
    saved.write_text(json.dumps(record))
    before=config.state_file.read_bytes()
    result=dispatch.adopt_worker(config.state_file)
    assert (result.state,result.return_code)==('unknown',3)
    assert config.state_file.read_bytes()==before
    assert not (tmp_path/'foreign.state').exists()


def test_event_storage_failure_keeps_terminal_journal_done_and_repairs_without_launch(tmp_path,monkeypatch):
    from ai_skills import dispatch
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    emit=dispatch.EventBus.emit
    def fail(bus,event,fields):
        if event=='dispatch-complete':raise OSError('injected event publication failure')
        return emit(bus,event,fields)
    monkeypatch.setattr(dispatch.EventBus,'emit',fail)
    with pytest.raises(OSError,match='injected event'):dispatch_worker(config)
    assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='success'
    assert json.loads(config.state_file.read_text())['state']=='completed'
    assert 'status=success' in config.agent_run.done_file.read_text()
    monkeypatch.setattr(dispatch.EventBus,'emit',emit)
    assert dispatch.adopt_worker(config.state_file).state=='completed'
    with pytest.raises(RuntimeError,match='prohibits relaunch'):dispatch_worker(config)


def test_result_capture_configuration_round_trip_and_rejections(tmp_path):
    explicit = {'required_checks': ['check A', 'check B'], 'review_comment_ids': []}
    inputs = {
        'state_file': tmp_path / 'state.json', 'result_capture': explicit,
        'final_response_file': 'caller.json',
        'agent_run': {'agent': 'codex', 'final_response_file': 'nested.json',
                      'result_capture_context': {'spoofed': True}},
    }
    config = WorkerConfig.from_sources(inputs, {'RUN_WITH_IT_FINAL_RESPONSE_FILE': 'parent.json'})
    assert config.result_capture == explicit
    assert config.agent_run.final_response_file is None
    assert config.agent_run.result_capture_context is None
    record = config.to_record()
    assert WorkerConfig.from_record(record).result_capture == explicit
    del record['result_capture']
    del record['agent_run']['final_response_file']
    del record['agent_run']['result_capture_context']
    assert WorkerConfig.from_record(record).result_capture is None
    assert WorkerConfig.from_sources({'state_file': config.state_file},
        {'RUN_WITH_IT_RESULT_CAPTURE': json.dumps(explicit)}).result_capture is None
    for bad in ([], {}, {'required_checks': ['a'], 'required_review_ids': []},
                *({**explicit, 'required_checks': checks} for checks in ([], ['a', 'a'], [' '], [1])),
                *({**explicit, 'review_comment_ids': ids} for ids in (
                    None, 'R1', ['R1', 'R1'], [''], [' '], [1], [['R1']], {'R1': True}))):
        with pytest.raises(ValueError):
            WorkerConfig.from_sources({**inputs, 'result_capture': bad}, {})
    modifier = {**explicit, 'review_comment_ids': ['R001', 'R002']}
    loaded = WorkerConfig.from_sources({**inputs, 'result_capture': modifier}, {})
    assert WorkerConfig.from_record(loaded.to_record()).result_capture == modifier
    assert not config.state_file.exists()


CAPTURE_CHECKS = {'required_checks': ['feature check', 'git check'], 'review_comment_ids': []}


def capture_worker(tmp_path, body, *, help_flag=True, probe=None, **options):
    from ai_skills.git import git_output
    from ai_skills.runner import load_registry

    repo = tmp_path / 'repo'
    repo.mkdir()
    git_output(str(repo), 'init', '-q')
    git_output(str(repo), 'config', 'user.name', 'Test')
    git_output(str(repo), 'config', 'user.email', 'test@example.invalid')
    git_output(str(repo), 'commit', '--allow-empty', '-qm', 'Baseline')
    baseline = git_output(str(repo), 'rev-parse', 'HEAD')
    (repo / '.git' / 'info' / 'exclude').write_text('/exec\n', encoding='utf-8')
    script = repo / 'exec'
    help_text = '--output-last-message <FILE>' if help_flag else 'Older Codex'
    probe = probe or f'''
if sys.argv[1:] == ['--help']:
    print({help_text!r})
    raise SystemExit(0)
'''
    script.write_text(f'''import json, os, subprocess, sys, time
from pathlib import Path
{probe}
result_path = Path(os.environ['RUN_WITH_IT_RESULT_FILE'])
state = Path(os.environ['RUN_WITH_IT_STATE_FILE'])
with state.with_name('calls').open('a') as calls:
    calls.write('run\\n')
args = sys.argv[1:]
prompt = sys.stdin.buffer.read()
metadata = json.loads(prompt.decode('utf-8').rsplit('Authoritative attempt metadata:\\n', 1)[1])
record = json.loads(Path(str(state) + '.config.json').read_text(encoding='utf-8'))
''' + body, encoding='utf-8')
    config = worker_config(tmp_path, '', issue_base_sha=baseline, **options)
    registry = load_registry()
    registry['agents']['codex']['invocation']['command'] = sys.executable
    registry['agents']['claude']['invocation']['command'] = sys.executable
    registry['agents']['claude']['invocation']['args_template'].insert(0, 'exec')
    config.agent_run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    run = config.agent_run.replace(agent='codex-cli', role='impl', cycle=4, repo_root=repo,
        unattended=True, record_directory=tmp_path / 'records', issue_dir=tmp_path / 'issue')
    return replace(config, agent_run=run)


@pytest.mark.parametrize('variant', ['supported', 'unsupported', 'legacy', 'claude', 'modify', 'review', 'plan',
                                     'no-cycle', 'impl-review-ids'])
def test_capture_binding_recorded_only_for_eligible_codex_attempts(tmp_path, variant):
    bound = variant in ('supported', 'modify')
    checks = CAPTURE_CHECKS if variant != 'impl-review-ids' else {**CAPTURE_CHECKS, 'review_comment_ids': ['R001']}
    config = capture_worker(tmp_path, f'''
assert ('result_capture' in metadata) == {bound}
assert ('--output-last-message' in args) == {bound}
if {bound}:
    assert args[-3:] == ['--output-last-message', record['agent_run']['final_response_file'], '-']
    assert Path(args[-2]) == Path(record['agent_run']['stream_directory']) / 'final-response.json'
    assert metadata['result_capture']['envelope']['cycle'] == '4'
    assert metadata['result_capture']['required_checks'] == ['feature check', 'git check']
else:
    assert record['agent_run']['final_response_file'] is None
    assert record['agent_run']['result_capture_context'] is None
result_path.with_name('transport-ok').touch()
if {variant == 'unsupported'}:
    result_path.write_text(json.dumps({{'issue': '7', 'role': 'impl', 'status': 'success',
        'no_op': True, 'commit_sha': 'NONE', 'files_committed': [],
        'verification': {{'passed': True, 'commands': ['direct report check']}}}}), encoding='utf-8')
''', help_flag=variant != 'unsupported', result_capture=None if variant == 'legacy' else checks)
    if variant in ('modify', 'review', 'plan'):
        config = replace(config, agent_run=config.agent_run.replace(role=variant))
    if variant == 'review':
        config = replace(config, agent_run=config.agent_run.replace(result_file=tmp_path / 'review-status.json'))
    if variant == 'no-cycle':
        config = replace(config, agent_run=config.agent_run.replace(cycle=''))
    if variant == 'claude':
        config = replace(config, agent_run=config.agent_run.replace(agent='claude'))
    result = dispatch_worker(config)
    assert (tmp_path / 'transport-ok').exists()
    if variant == 'unsupported':
        assert result.state == 'completed'
        assert json.loads(config.agent_run.result_file.read_bytes())['no_op'] is True


@pytest.mark.parametrize('kind', ['file', 'symlink', 'alias', 'conflicting-arg'])
def test_admission_rejects_occupied_or_aliased_capture_path(tmp_path, kind):
    config = capture_worker(tmp_path, '', result_capture=CAPTURE_CHECKS)
    path = Path(str(config.state_file) + '.streams') / 'final-response.json'
    path.parent.mkdir()
    if kind == 'file':
        path.write_text('preserve', encoding='utf-8')
    elif kind == 'symlink':
        if os.name == 'nt':
            pytest.skip('Windows test process may lack symlink privilege')
        path.symlink_to(tmp_path / 'absent')
    elif kind == 'alias':
        config = replace(config, agent_run=config.agent_run.replace(log_file=path))
    else:
        config = replace(config, agent_run=config.agent_run.replace(extra_args=('--output-last-message=other',)))
    with pytest.raises((RuntimeError, ValueError), match='capture|collision'):
        dispatch_worker(config)
    assert not config.state_file.exists()
    assert not (tmp_path / 'calls').exists()
    if kind == 'file':
        assert path.read_text(encoding='utf-8') == 'preserve'
    if kind == 'symlink':
        assert path.is_symlink()


BUILD_CAPTURE = '''
contract = metadata['result_capture']
capture = Path(args[-2])
assert args[-3:] == ['--output-last-message', str(capture), '-']
Path('feature.txt').write_text('implemented', encoding='utf-8')
subprocess.run(['git', 'add', 'feature.txt'], check=True)
subprocess.run(['git', 'commit', '-qm', 'Implement feature'], check=True)
assert Path('feature.txt').read_text(encoding='utf-8') == 'implemented'
subprocess.run(['git', 'diff', '--check', 'HEAD^', 'HEAD'], check=True)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
report = {'schema_version': 1, 'issue': '7', 'role': 'impl', 'status': 'success',
    'commit_sha': head, 'files_committed': ['feature.txt'], 'plan_deviations': [],
    'verification': {'passed': True, 'commit_sha': head, 'commands': ['feature check', 'git check'],
        'checks': [{'command': check, 'passed': True, 'evidence': 'observed exit 0'}
                   for check in contract['required_checks']]}}
envelope = {**contract['envelope'], 'result': report}
'''


def test_fake_codex_captured_commit_becomes_verified_result_after_one_invocation(tmp_path):
    from ai_skills.dispatch import adopt_worker
    from ai_skills.git import current_head, git_output

    config = capture_worker(tmp_path, BUILD_CAPTURE + '''
capture.write_text(json.dumps(envelope), encoding='utf-8')
assert not result_path.exists()
''', result_capture=CAPTURE_CHECKS)
    config = WorkerConfig.from_sources({**config.to_record(), 'agent_run': config.agent_run}, os.environ)
    root = str(config.agent_run.repo_root)
    git_output(root, 'commit', '--allow-empty', '-qm', 'Attempt starting checkpoint')
    pre_spawn_head = current_head(root)
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    captured_bytes = capture.read_bytes()
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report == json.loads(captured_bytes)['result']
    assert report['commit_sha'] == current_head(root)
    assert report['commit_sha'] != pre_spawn_head != config.issue_base_sha
    assert report['verification']['commands'] == CAPTURE_CHECKS['required_checks']
    journal = Path(str(config.state_file) + '.outcome.json')
    journal_bytes = journal.read_bytes()
    assert json.loads(journal_bytes)['status'] == 'success'
    assert json.loads(journal_bytes)['git_progress']['pre_spawn_head'] == pre_spawn_head
    assert '|status=success|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    assert adopt_worker(config.state_file).state == 'completed'
    assert journal.read_bytes() == journal_bytes
    assert capture.read_bytes() == captured_bytes
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('change_during_read', [False, True])
def test_capture_handles_distinct_path_and_descriptor_ctimes(tmp_path, monkeypatch, change_during_read):
    from ai_skills import dispatch

    directory = tmp_path.resolve()
    path = directory / 'final-response.json'
    payload = b'{"captured":true}'
    path.write_bytes(payload)
    run = replace(worker_config(tmp_path, '').agent_run, role='impl',
                  dispatcher_managed=True, result_capture_context={},
                  stream_directory=directory, final_response_file=path)
    path_ctime = path.lstat().st_ctime_ns
    fstat = os.fstat
    extract_response = dispatch.extract_response
    read_finished = False

    def descriptor_stat(fd):
        info = fstat(fd)
        # Windows path stats report creation time in ctime. Descriptor stats
        # report the metadata change time, which can differ for the same file.
        return os.stat_result(info, {
            'st_mtime_ns': info.st_mtime_ns,
            'st_ctime_ns': path_ctime + (2 if read_finished and change_during_read else 1),
        })

    def read_response(source, stream):
        nonlocal read_finished
        response = extract_response(source, stream)
        read_finished = True
        return response

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fstat', descriptor_stat)
        patch.setattr(dispatch, 'extract_response', read_response)
        if change_during_read:
            with pytest.raises(OSError, match='capture changed while reading'):
                dispatch._capture_bytes(run, None, None)
            assert read_finished
        else:
            assert dispatch._capture_bytes(run, None, None) == payload


@pytest.mark.parametrize('mutation', [
    "envelope['run_id'] = 'stale'",
    "envelope['attempt_id'] = 'stale'",
    "envelope['issue'] = '8'",
    "envelope['role'] = 'modify'",
    "envelope['cycle'] = 4",
    "envelope['schema_version'] = True",
    "envelope['artifact_kind'] = 'other'",
    "report['issue'] = '8'",
    "report.update(artifact_kind='role_failure', status='failed')",
    "report['no_op'] = True",
    "report['commit_sha'] = head[:12]",
    "report['commit_sha'] = record['pre_spawn_head']",
    "subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'Later HEAD'], check=True)",
    "subprocess.run(['git', 'checkout', '--orphan', 'unrelated'], check=True); "
    "subprocess.run(['git', 'commit', '-qm', 'Unrelated HEAD'], check=True); "
    "head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(); "
    "report['commit_sha'] = report['verification']['commit_sha'] = head",
    "Path('feature.txt').write_text('dirty')",
    "subprocess.run(['git', 'config', 'status.showUntrackedFiles', 'no'], check=True); Path('untracked').touch()",
    "report['verification'].pop('passed')",
    "report['verification']['commit_sha'] = record['pre_spawn_head']",
    "report['verification']['checks'].pop()",
    "report['verification']['checks'][1] = report['verification']['checks'][0]",
    "report['verification']['checks'][0]['passed'] = False",
    "report['verification']['checks'][0]['evidence'] = ' '",
    "report['verification']['checks'][0]['command'] = 'feature check '",
    "raw = b'{malformed'",
    "raw = bytes([255])",
    "raw = b'```json\\n' + raw + b'\\n```'",
    "raw = raw + raw",
    "raw = b'[]'",
    "raw = b'{\"schema_version\":1,' + raw[1:]",
    "raw = raw[:-1] + b',\"nonfinite\":NaN}'",
])
def test_captured_result_rejections_never_publish_success(tmp_path, mutation):
    encode = "raw = json.dumps(envelope).encode('utf-8')"
    lines = (encode, mutation) if mutation.startswith('raw =') else (mutation, encode)
    config = capture_worker(tmp_path, BUILD_CAPTURE + f'''
{lines[0]}
{lines[1]}
capture.write_bytes(raw)
result_path.with_name('capture-original').write_bytes(raw)
''', result_capture=CAPTURE_CHECKS)
    result = dispatch_worker(config)
    assert result.state != 'completed'
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    assert capture.read_bytes() == (tmp_path / 'capture-original').read_bytes()
    if config.agent_run.result_file.exists():
        assert json.loads(config.agent_run.result_file.read_bytes())['status'] == 'artifact_recovery_required'
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    diagnostic = next(line for line in events.splitlines() if 'type=dispatch-capture-rejected|' in line)
    assert 'run_id=worker-7-impl-' in diagnostic and '|attempt_id=' in diagnostic
    assert 'reason=' in diagnostic and 'observed exit 0' not in diagnostic
    if mutation.startswith('raw =') and mutation != "raw = b'[]'":
        assert 'reason=capture-invalid-json' in diagnostic


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'stream-alias'])
def test_unsafe_capture_locations_are_never_read_as_results(tmp_path, kind):
    if os.name == 'nt' and kind in ('symlink', 'stream-alias'):
        pytest.skip('Windows test process may lack symlink privilege')
    config = capture_worker(tmp_path, BUILD_CAPTURE + f'''
target = result_path.with_name('external-response')
target.write_text(json.dumps(envelope), encoding='utf-8')
kind = {kind!r}
if kind == 'symlink':
    capture.symlink_to(target)
elif kind == 'hardlink':
    os.link(target, capture)
elif kind == 'directory':
    capture.mkdir()
else:
    capture.parent.rename(result_path.with_name('displaced-streams'))
    capture.parent.symlink_to(result_path.with_name('displaced-streams'), target_is_directory=True)
    capture.write_bytes(target.read_bytes())
''', result_capture=CAPTURE_CHECKS)
    assert dispatch_worker(config).state == 'artifact-recovery-required'
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    assert 'reason=capture-path-unsafe' in events
    assert json.loads((tmp_path / 'external-response').read_bytes())['result']['status'] == 'success'


@pytest.mark.parametrize('kind', ['nonzero', 'stop', 'zero-exit-with-stop', 'runner-failure',
                                  'runner-nonzero', 'no-durable-exit'])
def test_captured_result_requires_successful_owned_exit(tmp_path, monkeypatch, kind):
    from ai_skills import dispatch

    suffix = {'nonzero': 'raise SystemExit(7)', 'stop': 'time.sleep(60)'}.get(kind, '')
    config = capture_worker(tmp_path, BUILD_CAPTURE + f'''
capture.write_text(json.dumps(envelope), encoding='utf-8')
{suffix}
''', result_capture=CAPTURE_CHECKS, hard_limit_seconds=2 if kind == 'stop' else 7200)

    original = dispatch.run_agent

    def invoke(run):
        result = original(run)
        if kind == 'runner-failure':
            raise RuntimeError('injected runner failure')
        if kind == 'runner-nonzero':
            return replace(result, return_code=1)
        if kind == 'zero-exit-with-stop':
            dispatch._record_stop_intent(replace(config, agent_run=run), 'hard-limit-exceeded')
        if kind == 'no-durable-exit':
            path = next(Path(run.record_directory).glob('*.json'))
            record = json.loads(path.read_bytes())
            del record['return_code']
            dispatch.write_json_atomic(path, record)
        return result

    monkeypatch.setattr(dispatch, 'run_agent', invoke)
    result = dispatch_worker(config)
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    assert json.loads(capture.read_bytes())['result']['status'] == 'success'
    state = json.loads(config.state_file.read_bytes())
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    if kind in ('stop', 'zero-exit-with-stop'):
        assert state['stop_intent']['reason'] == 'hard-limit-exceeded'
    if kind == 'zero-exit-with-stop':
        assert outcome['provider_return_code'] == 0
    if kind == 'no-durable-exit':
        assert result.reason == 'provider-exit-status-unknown'
        assert outcome['status'] == 'pending'
        assert not config.agent_run.result_file.exists()
        assert not config.agent_run.done_file.exists()
    else:
        assert result.state != 'completed'
        assert outcome['status'] == 'failed'
        assert '|status=failed|' in config.agent_run.done_file.read_text(encoding='utf-8')


@pytest.mark.parametrize('kind', ['valid', 'malformed', 'empty', 'race'])
def test_existing_result_path_retains_precedence_over_capture(tmp_path, monkeypatch, kind):
    direct = {'valid': "json.dumps({**report, 'publication': 'direct'})",
              'malformed': "'{broken'", 'empty': "''", 'race': None}[kind]
    suffix = '' if direct is None else f'result_path.write_text({direct}, encoding="utf-8")'
    config = capture_worker(tmp_path, BUILD_CAPTURE + f'''
capture.write_text(json.dumps(envelope), encoding='utf-8')
{suffix}
''', result_capture=CAPTURE_CHECKS)
    if kind == 'race':
        original_link = os.link

        def concurrent_publication(source, destination, *args, **kwargs):
            if Path(destination) == config.agent_run.result_file:
                report = json.loads(Path(source).read_bytes())
                Path(destination).write_text(json.dumps({**report, 'publication': 'direct'}), encoding='utf-8')
            return original_link(source, destination, *args, **kwargs)

        monkeypatch.setattr(os, 'link', concurrent_publication)
    result = dispatch_worker(config)
    result_bytes = config.agent_run.result_file.read_bytes()
    if kind in ('valid', 'race'):
        assert result.state == 'completed'
        assert json.loads(result_bytes)['publication'] == 'direct'
    else:
        assert result.state == 'failed'
        assert result_bytes == (b'{broken' if kind == 'malformed' else b'')


PUBLICATION_BOUNDARIES = ('result', 'outcome', 'state', 'done')


def interrupt_publication(monkeypatch, config, boundary):
    from ai_skills import dispatch

    write_json, write_text = dispatch.write_json_atomic, dispatch.write_text_atomic
    state_file = config.state_file.resolve()
    done_file = config.agent_run.done_file.resolve()
    result_file = config.agent_run.result_file.resolve()

    def publish_json(path, value):
        if boundary == 'outcome' and Path(path).resolve() == dispatch.outcome_path(state_file) and value['status'] != 'pending':
            raise OSError('injected publication failure')
        if boundary == 'state' and Path(path).resolve() == state_file and 'completed_at' in value:
            raise OSError('injected publication failure')
        return write_json(path, value)

    def publish_text(path, value, *, exclusive=False):
        if ((boundary == 'result' and exclusive and Path(path).resolve() == result_file) or
                (boundary == 'done' and Path(path).resolve() == done_file)):
            raise OSError('injected publication failure')
        return write_text(path, value, exclusive=exclusive)

    monkeypatch.setattr(dispatch, 'write_json_atomic', publish_json)
    monkeypatch.setattr(dispatch, 'write_text_atomic', publish_text)


def interrupt_foreground_dispatch(monkeypatch, config, boundary):
    with monkeypatch.context() as patch:
        interrupt_publication(patch, config, boundary)
        with pytest.raises(OSError, match='injected publication failure'):
            dispatch_worker(config)


def mark_launcher_dead(config):
    from ai_skills.storage import load_json, write_json_atomic

    state = load_json(config.state_file)
    state['launcher_identity']['created_at'] += 1
    write_json_atomic(config.state_file, state)


def assert_publication_boundary(config, boundary, status):
    from ai_skills.dispatch import outcome_path

    state = json.loads(config.state_file.read_bytes())
    journal = json.loads(outcome_path(config.state_file).read_bytes())
    assert config.agent_run.result_file.exists() == (boundary != 'result')
    assert journal['status'] == (status if boundary in ('state', 'done') else 'pending')
    assert ('completed_at' in state) == (boundary == 'done')
    assert not config.agent_run.done_file.exists()


def assert_capture_adopted_twice(monkeypatch, config, kind='commit'):
    from ai_skills import dispatch
    from ai_skills.git import current_head

    run = config.agent_run
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    journal = dispatch.outcome_path(config.state_file)
    capture_before = capture.read_bytes()
    result_before = run.result_file.read_bytes() if run.result_file.exists() else None
    journal_before = journal.read_bytes()
    terminal = json.loads(journal_before)['status'] != 'pending'
    status, state, code = ('failed', 'failed', 1) if kind == 'typed' else ('success', 'completed', 0)
    write_json = dispatch.write_json_atomic

    def forbid_capture(*args, **kwargs):
        pytest.fail('adoption attempted to import capture after publication')

    def preserve_journal(path, value):
        assert path != journal.resolve(), 'adoption wrote a second terminal decision'
        return write_json(path, value)

    with monkeypatch.context() as patch:
        if result_before is not None:
            patch.setattr(dispatch, '_capture_bytes', forbid_capture)
        if terminal:
            patch.setattr(dispatch, '_publish_captured_result', forbid_capture)
            patch.setattr(dispatch, 'write_json_atomic', preserve_journal)
        first = dispatch.adopt_worker(config.state_file)
        assert (first.state, first.return_code) == (state, code), first.reason
        result_bytes, journal_bytes = run.result_file.read_bytes(), journal.read_bytes()
        done_bytes = run.done_file.read_bytes()
        patch.setattr(dispatch, '_publish_captured_result', forbid_capture)
        patch.setattr(dispatch, 'write_json_atomic', preserve_journal)
        assert dispatch.adopt_worker(config.state_file) == first

    assert run.result_file.read_bytes() == (result_before if result_before is not None else result_bytes)
    assert journal.read_bytes() == journal_bytes
    assert run.done_file.read_bytes() == done_bytes
    assert capture.read_bytes() == capture_before
    outcome = json.loads(journal_bytes)
    if terminal:
        assert journal_bytes == journal_before
    assert (outcome['status'], outcome['provider_return_code'], outcome['worker_return_code']) == (status, 0, code)
    assert f'|status={status}|source=dispatcher|'.encode() in done_bytes
    expected = json.loads(capture_before)['result']
    if kind == 'direct':
        expected = {**expected, 'publication': 'direct'}
        assert 'dispatch-capture-rejected' not in run.events_log.read_text(encoding='utf-8')
    assert json.loads(result_bytes) == expected
    head = current_head(str(run.repo_root))
    assert outcome['git_progress']['head'] == head
    if kind == 'typed':
        assert outcome['reason'] == expected['reason']
    elif kind == 'noop':
        assert expected['commit_sha'] == 'NONE' and expected['files_committed'] == []
        assert head == expected['verification']['commit_sha'] == config.pre_spawn_head
    else:
        assert expected['commit_sha'] == expected['verification']['commit_sha'] == head
        assert head != outcome['git_progress']['pre_spawn_head']
    assert config.state_file.with_name('calls').read_text(encoding='utf-8') == 'run\n'
    with pytest.raises(RuntimeError, match='prohibits relaunch'):
        dispatch_worker(config)


def publication_worker(tmp_path, kind='commit', *, suffix='', **options):
    if kind == 'noop':
        return noop_worker(tmp_path)
    if kind == 'typed':
        return typed_capture_worker(tmp_path)
    body = BUILD_CAPTURE + "capture.write_text(json.dumps(envelope), encoding='utf-8')\n"
    if kind == 'direct':
        body += "result_path.write_text(json.dumps({**report, 'publication': 'direct'}), encoding='utf-8')\n"
    return capture_worker(tmp_path, body + suffix, result_capture=CAPTURE_CHECKS, **options)


@pytest.mark.parametrize(('kind', 'boundary'), [
    (kind, boundary)
    for kind in ('commit', 'noop', 'typed', 'direct')
    for boundary in PUBLICATION_BOUNDARIES
    if (kind, boundary) != ('direct', 'result')
])
def test_adoption_reconciles_each_capture_publication_boundary_once(tmp_path, monkeypatch, kind, boundary):
    from ai_skills.git import git_output

    config = publication_worker(tmp_path, kind)
    root = str(config.agent_run.repo_root)
    before = git_output(root, 'rev-list', '--count', 'HEAD') if kind == 'noop' else None
    interrupt_foreground_dispatch(monkeypatch, config, boundary)
    assert_publication_boundary(config, boundary, 'failed' if kind == 'typed' else 'success')
    mark_launcher_dead(config)
    assert_capture_adopted_twice(monkeypatch, config, kind)
    if kind == 'noop':
        assert git_output(root, 'rev-list', '--count', 'HEAD') == before


@pytest.mark.parametrize('uncertainty', ['provider-unknown', 'exit-unknown', 'launcher-alive'])
def test_uncertain_ownership_defers_boundary_adoption_without_publication(tmp_path, monkeypatch, uncertainty):
    from ai_skills import dispatch

    config = publication_worker(tmp_path)
    interrupt_foreground_dispatch(monkeypatch, config, 'result')
    journal = dispatch.outcome_path(config.state_file)
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    before = (journal.read_bytes(), capture.read_bytes())
    if uncertainty != 'launcher-alive':
        mark_launcher_dead(config)
    with monkeypatch.context() as patch:
        if uncertainty == 'provider-unknown':
            inspect = dispatch.ProcessSupervisor.inspect

            def inspect_unknown(supervisor, identity):
                liveness = inspect(supervisor, identity)
                assert liveness.state == 'dead'
                return replace(liveness, state='unknown', reason='ownership-inaccessible')

            patch.setattr(dispatch.ProcessSupervisor, 'inspect', inspect_unknown)
            expected = ('unknown', 3, 'ownership-inaccessible')
        elif uncertainty == 'exit-unknown':
            patch.setattr(dispatch.ProcessSupervisor, 'return_code', lambda self, identity: None)
            expected = ('unknown', 3, 'provider-exit-status-unknown')
        else:
            expected = ('running', 0, 'launcher-ownership-alive')
        adopted = dispatch.adopt_worker(config.state_file)
        assert (adopted.state, adopted.return_code, adopted.reason) == expected

    assert_publication_boundary(config, 'result', 'success')
    assert (journal.read_bytes(), capture.read_bytes()) == before
    mark_launcher_dead(config)
    assert_capture_adopted_twice(monkeypatch, config)


@pytest.mark.parametrize('boundary', PUBLICATION_BOUNDARIES)
def test_adoption_storage_failure_propagates_then_completes(tmp_path, monkeypatch, boundary):
    from ai_skills import dispatch

    config = publication_worker(tmp_path)
    interrupt_foreground_dispatch(monkeypatch, config, 'result')
    mark_launcher_dead(config)
    with monkeypatch.context() as patch:
        interrupt_publication(patch, config, boundary)
        with pytest.raises(OSError, match='injected publication failure'):
            dispatch.adopt_worker(config.state_file)
    assert_publication_boundary(config, boundary, 'success')
    assert_capture_adopted_twice(monkeypatch, config)


CRASH_DURING_PUBLICATION = """
import os, sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from ai_skills import dispatch
from ai_skills.storage import load_json

config = dispatch.WorkerConfig.from_record(load_json(Path(sys.argv[2])))
boundary = sys.argv[3]
write_json, write_text = dispatch.write_json_atomic, dispatch.write_text_atomic

def publish_json(path, value):
    if boundary == 'outcome' and path == dispatch.outcome_path(config.state_file) and value['status'] != 'pending':
        os._exit(97)
    return write_json(path, value)

def publish_text(path, value, *, exclusive=False):
    if boundary == 'result' and exclusive:
        os._exit(97)
    return write_text(path, value, exclusive=exclusive)

dispatch.write_json_atomic = publish_json
dispatch.write_text_atomic = publish_text
raise SystemExit(dispatch._work(config).return_code)
"""


def wait_for_crashed_wrapper(config):
    from ai_skills.processes import ProcessIdentity, ProcessSupervisor

    path = Path(str(config.state_file) + '.dispatcher.process.json')
    wrapper = json.loads(path.read_bytes())
    supervisor = ProcessSupervisor(Path(wrapper['record_directory']))
    identity = ProcessIdentity(**wrapper['identity'])
    deadline = time.monotonic() + 15
    while supervisor.inspect(identity).state != 'dead':
        assert time.monotonic() < deadline, 'dispatcher did not reach the publication boundary'
        time.sleep(.02)


@pytest.mark.parametrize('boundary', ['result', 'outcome'])
def test_detached_wrapper_loss_at_capture_boundary_adopts_once(tmp_path, monkeypatch, boundary):
    from ai_skills.dispatch import _stop_provider
    from ai_skills.processes import ProcessSupervisor

    release = tmp_path / 'release-provider'
    config = publication_worker(tmp_path, detach=True, suffix='''
while not state.with_name('release-provider').exists():
    time.sleep(.01)
''')
    start = ProcessSupervisor.start

    def start_with_crash(supervisor, spec, *, run_id, attempt_id):
        if run_id.endswith('-dispatcher'):
            spec = replace(spec, argv=(*spec.argv[:2], CRASH_DURING_PUBLICATION, *spec.argv[3:], boundary))
        return start(supervisor, spec, run_id=run_id, attempt_id=attempt_id)

    monkeypatch.setattr(ProcessSupervisor, 'start', start_with_crash)
    try:
        result = dispatch_worker(config)
        assert result.state in ('running', 'quiet', 'stalled'), result.reason
        release.touch()
        wait_for_crashed_wrapper(config)
        assert_publication_boundary(config, boundary, 'success')
        lock = Path(str(config.state_file) + '.lock')
        assert (lock / 'owner.json').is_file()
        assert_capture_adopted_twice(monkeypatch, config)
        assert not lock.exists()
    finally:
        release.touch()
        _stop_provider(config)


def test_adoption_repairs_prelaunch_failure_after_terminal_journal(tmp_path, monkeypatch):
    from ai_skills import dispatch

    config = worker_config(tmp_path, 'raise AssertionError("provider should never run")')
    registry = json.loads(config.agent_run.registry_file.read_bytes())
    registry['agents']['fake']['invocation']['command'] = str(tmp_path / 'missing-provider')
    config.agent_run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    interrupt_foreground_dispatch(monkeypatch, config, 'state')
    mark_launcher_dead(config)
    journal = dispatch.outcome_path(config.state_file)
    before = journal.read_bytes()
    outcome = json.loads(before)
    assert (outcome['status'], outcome['provider_return_code'], outcome['reason']) == ('failed', None, 'missing-command')
    assert not config.agent_run.done_file.exists()

    for _ in range(2):
        result = dispatch.adopt_worker(config.state_file)
        assert (result.state, result.return_code, result.reason) == ('failed', 127, 'missing-command')
        assert journal.read_bytes() == before
        assert '|status=failed|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    assert not Path(str(config.state_file) + '.process.json').exists()


@pytest.mark.parametrize('finish', ['foreground', 'adoption', 'direct-result'])
def test_capture_requires_runner_record_after_process_state_write_fails(tmp_path, monkeypatch, finish):
    from ai_skills import dispatch, runner
    from ai_skills.processes import ProcessIdentity, ProcessSupervisor

    direct = "result_path.write_text(json.dumps(dict(report, publication='direct')), encoding='utf-8')"
    config = capture_worker(tmp_path, BUILD_CAPTURE + f'''
capture.write_text(json.dumps(envelope), encoding='utf-8')
{direct if finish == 'direct-result' else ''}
''', result_capture=CAPTURE_CHECKS)
    process_file = Path(str(config.state_file) + '.process.json')
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    journal = Path(str(config.state_file) + '.outcome.json')
    captured = []

    def fail_initial_process_write(path, value):
        assert path == process_file and value['state'] == 'alive'
        supervisor = ProcessSupervisor(Path(value['record_directory']))
        identity = ProcessIdentity(**value['identity'])
        deadline = time.monotonic() + 5
        while supervisor.return_code(identity) is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert supervisor.return_code(identity) == 0
        captured.append(capture.read_bytes())
        raise OSError('injected runner process-state write failure')

    monkeypatch.setattr(runner, 'write_json_atomic', fail_initial_process_write)
    if finish == 'adoption':
        def interrupt_publication(*args, **kwargs):
            raise OSError('injected result storage interruption')

        with monkeypatch.context() as interruption:
            interruption.setattr(dispatch, 'write_text_atomic', interrupt_publication)
            interruption.setattr(dispatch.artifacts, 'write_json_atomic', interrupt_publication)
            with pytest.raises(OSError, match='injected result storage interruption'):
                dispatch_worker(config)
        assert json.loads(journal.read_bytes())['status'] == 'pending'
        assert not config.agent_run.result_file.exists()
        assert not config.agent_run.done_file.exists()
        monkeypatch.setattr(dispatch, '_launcher_state', lambda state: 'dead')
        result = dispatch.adopt_worker(config.state_file)
        assert 'reason=capture-runner-exit-unconfirmed' in config.agent_run.events_log.read_text(encoding='utf-8')
    else:
        result = dispatch_worker(config)

    assert not process_file.exists()
    assert captured == [capture.read_bytes()]
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'
    outcome = json.loads(journal.read_bytes())
    assert outcome['provider_return_code'] == 0
    report = json.loads(config.agent_run.result_file.read_bytes())
    done = config.agent_run.done_file.read_text(encoding='utf-8')
    if finish == 'direct-result':
        assert result.state == 'completed'
        assert report == {**json.loads(captured[0])['result'], 'publication': 'direct'}
        assert outcome['status'] == 'success' and '|status=success|' in done
    else:
        assert result.state == 'artifact-recovery-required'
        assert report['status'] == 'artifact_recovery_required'
        assert outcome['status'] == 'failed' and '|status=failed|' in done


def test_runner_preserves_capture_created_between_admission_and_launch(tmp_path, monkeypatch):
    from ai_skills import dispatch

    config = capture_worker(tmp_path, '', result_capture=CAPTURE_CHECKS)
    original = dispatch.run_agent

    def occupy(run):
        path = Path(run.final_response_file)
        path.parent.mkdir()
        path.write_bytes(b'preserve these bytes')
        return original(run)

    monkeypatch.setattr(dispatch, 'run_agent', occupy)
    assert dispatch_worker(config).state == 'failed'
    assert not (tmp_path / 'calls').exists()
    path = Path(str(config.state_file) + '.streams') / 'final-response.json'
    assert path.read_bytes() == b'preserve these bytes'


def test_capture_cannot_publish_through_a_redirected_result_path(tmp_path):
    if os.name == 'nt':
        pytest.skip('Windows test process may lack symlink privilege')
    config = capture_worker(tmp_path, BUILD_CAPTURE + '''
capture.write_text(json.dumps(envelope), encoding='utf-8')
result_path.parent.mkdir()
external = result_path.parent.with_name('external-output')
external.mkdir()
result_path.parent.rmdir()
result_path.parent.symlink_to(external, target_is_directory=True)
''', result_capture=CAPTURE_CHECKS)
    run = config.agent_run.replace(result_file=tmp_path / 'reports' / 'result.json')
    result = dispatch_worker(replace(config, agent_run=run))
    assert result.reason == 'invalid-or-conflicting-outcome'
    assert list((tmp_path / 'external-output').iterdir()) == []
    assert not run.done_file.exists()


BUILD_NOOP_CAPTURE = '''
contract = metadata['result_capture']
capture = Path(args[-2])
assert Path('feature.txt').read_text(encoding='utf-8') == 'implemented'
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
report = {'schema_version': 1, 'issue': '7', 'role': 'impl', 'status': 'success', 'no_op': True,
    'commit_sha': 'NONE', 'files_committed': [], 'plan_deviations': [],
    'verification': {'passed': True, 'commit_sha': head, 'commands': ['feature check', 'git check'],
        'checks': [{'command': check, 'passed': True, 'evidence': 'observed exit 0'}
                   for check in contract['required_checks']]}}
envelope = {**contract['envelope'], 'result': report}
'''


def noop_worker(tmp_path, suffix=''):
    from ai_skills.git import git_output

    config = capture_worker(tmp_path, BUILD_NOOP_CAPTURE + suffix + '''
capture.write_bytes(json.dumps(envelope).encode('utf-8'))
''', result_capture=CAPTURE_CHECKS)
    root = str(config.agent_run.repo_root)
    (config.agent_run.repo_root / 'feature.txt').write_text('implemented', encoding='utf-8')
    git_output(root, 'add', 'feature.txt')
    git_output(root, 'commit', '-qm', 'Already implemented')
    return replace(config, pre_spawn_head=git_output(root, 'rev-parse', 'HEAD'))


def test_captured_noop_publishes_without_commit(tmp_path):
    from ai_skills.dispatch import adopt_worker
    from ai_skills.git import current_head, git_output

    config = noop_worker(tmp_path)
    root = str(config.agent_run.repo_root)
    head, count = current_head(root), git_output(root, 'rev-list', '--count', 'HEAD')
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report == json.loads(capture.read_bytes())['result']
    assert report['commit_sha'] == 'NONE' and report['files_committed'] == []
    assert report['verification']['commit_sha'] == head != config.issue_base_sha
    assert current_head(root) == head and git_output(root, 'rev-list', '--count', 'HEAD') == count
    assert not git_output(root, 'status', '--porcelain')
    assert '|status=success|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    assert adopt_worker(config.state_file).state == 'completed'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('mutation', [
    "report.pop('no_op')",
    "report['no_op'] = False",
    "report['no_op'] = 1",
    "report.pop('commit_sha')",
    "report['commit_sha'] = head",
    "report.pop('files_committed')",
    "report['files_committed'] = None",
    "report['files_committed'] = ['feature.txt']",
    "subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'Later'], check=True); "
    "report['verification']['commit_sha'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()",
    "Path('feature.txt').write_text('dirty')",
    "Path('feature.txt').write_text('dirty'); subprocess.run(['git', 'add', 'feature.txt'], check=True)",
    "subprocess.run(['git', 'config', 'status.showUntrackedFiles', 'no'], check=True); Path('untracked').touch()",
    "report['verification'].pop('passed')",
    "report['verification']['passed'] = False",
    "report['verification'].pop('commit_sha')",
    "report['verification']['commit_sha'] = 'NONE'",
    "report['verification']['commit_sha'] = head[:12]",
    "report['verification']['commit_sha'] = 'f' * 40",
    "report['verification'].pop('checks')",
    "report['verification']['checks'].pop()",
    "report['verification']['checks'].append(dict(report['verification']['checks'][0]))",
    "report['verification']['checks'].append({**report['verification']['checks'][0], 'command': 'extra'})",
    "report['verification']['checks'][0]['command'] = 'other check'",
    "report['verification']['checks'][0]['passed'] = False",
    "report['verification']['checks'][0]['evidence'] = ' '",
])
def test_captured_noop_rejections_never_publish_success(tmp_path, mutation):
    config = noop_worker(tmp_path, mutation)
    result = dispatch_worker(config)
    assert result.state != 'completed'
    if config.agent_run.result_file.exists():
        assert json.loads(config.agent_run.result_file.read_bytes())['status'] == 'artifact_recovery_required'
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    assert 'type=dispatch-capture-rejected|' in events


BUILD_TYPED_CAPTURE = '''
capture = Path(args[-2])
identity = metadata['result_capture']['envelope']
assert args[-3:] == ['--output-last-message', str(capture), '-']
report = {key: identity[key] for key in ('schema_version', 'issue', 'role', 'run_id', 'attempt_id')}
report.update(artifact_kind='role_failure', status='failed', reason='artifact-recovery-required',
              summary='The feature check failed.', verification={'passed': False},
              evidence=['feature check exited 1'])
envelope = {**identity, 'result': report}
'''


def typed_capture_worker(tmp_path, *, prepare='', encode='', after=''):
    return capture_worker(tmp_path, BUILD_TYPED_CAPTURE + prepare + '''
raw = json.dumps(envelope).encode('utf-8')
''' + encode + '''
capture.write_bytes(raw)
result_path.with_name('capture-original').write_bytes(raw)
''' + after, result_capture=CAPTURE_CHECKS)


def read_typed_capture(config):
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    raw = capture.read_bytes()
    assert raw == config.agent_run.result_file.with_name('capture-original').read_bytes()
    return raw


@pytest.mark.parametrize(('status', 'worktree', 'code'), [
    ('failed', 'clean', 1), ('failed', 'modified', 1), ('blocked', 'staged', 1),
    ('interrupted', 'untracked', 130),
])
def test_captured_role_failure_preserves_report_without_success_evidence(tmp_path, status, worktree, code):
    from ai_skills.git import current_head, git_output

    details = {
        'failed': {},
        'blocked': {'status': 'blocked', 'reason': 'service-unavailable',
                    'summary': 'Required database unavailable.', 'evidence': ['database connection refused']},
        'interrupted': {'status': 'interrupted', 'reason': 'operator-stopped-work',
                        'summary': 'Stopped before checks.', 'evidence': ['partial feature remains']},
    }
    config = typed_capture_worker(tmp_path, prepare=f'\nreport.update({details[status]!r})\n')
    repo = config.agent_run.repo_root
    if worktree != 'clean':
        (repo / 'feature.txt').write_text('partial work', encoding='utf-8')
    if worktree in ('modified', 'staged'):
        git_output(str(repo), 'add', 'feature.txt')
    if worktree == 'modified':
        git_output(str(repo), 'commit', '-qm', 'Existing feature')
        (repo / 'feature.txt').write_text('edited feature', encoding='utf-8')
    head, dirty = current_head(str(repo)), git_output(str(repo), 'status', '--porcelain')

    result = dispatch_worker(config)

    report = json.loads(read_typed_capture(config))['result']
    assert json.loads(config.agent_run.result_file.read_bytes()) == report
    assert 'commit_sha' not in report
    assert result.state == ('interrupted' if status == 'interrupted' else 'failed')
    assert result.reason == report['reason'] and result.return_code == code
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert (outcome['status'], outcome['reason'], outcome['provider_return_code']) == (status, report['reason'], 0)
    assert f'|status={status}|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    assert current_head(str(repo)) == head and git_output(str(repo), 'status', '--porcelain') == dirty
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize(('location', 'key', 'value'), [
    (location, key, value) for location in ('envelope', 'report') for key, value in (
        ('run_id', 'foreign'), ('attempt_id', 'foreign'), ('issue', '99'), ('role', 'modify'),
        ('schema_version', True),
    )
] + [('envelope', 'cycle', 4)])
def test_captured_role_failure_rejects_invalid_identity(tmp_path, key, value, location):
    config = typed_capture_worker(tmp_path, prepare=f'\n{location}[{key!r}] = {value!r}\n')
    reason = 'capture-identity-mismatch' if location == 'envelope' else (
        'invalid-role-failure-schema' if key == 'schema_version' else 'role-failure-identity-mismatch')
    assert_typed_capture_rejected(config, reason)


@pytest.mark.parametrize(('mutation', 'reason'), [
    ("report['status'] = 'success'", 'invalid-role-failure-status'),
    ("report['reason'] = ''", 'invalid-role-failure-reason'),
    ("report['summary'] = ' '", 'invalid-role-failure-reason'),
    ("report['evidence'] = [7]", 'invalid-role-failure-evidence'),
    ("report['verification']['passed'] = True", 'invalid-role-failure-verification'),
])
def test_captured_role_failure_rejects_invalid_schema(tmp_path, mutation, reason):
    config = typed_capture_worker(tmp_path, prepare=f'\n{mutation}\n')
    assert_typed_capture_rejected(config, reason)


@pytest.mark.parametrize('mutation', [
    "raw = raw.replace(b'\"status\":', b'\"status\": \"failed\", \"status\":', 1)",
    "raw = raw.replace(b'\"passed\": false', b'\"passed\": 1e999', 1)",
    "raw = raw[:-1]",
])
def test_captured_role_failure_rejects_invalid_json(tmp_path, mutation):
    config = typed_capture_worker(tmp_path, encode=f'\n{mutation}\n')
    assert_typed_capture_rejected(config, 'capture-invalid-json')


def assert_typed_capture_rejected(config, reason):
    assert dispatch_worker(config).state == 'failed'
    read_typed_capture(config)
    assert not config.agent_run.result_file.exists()
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    diagnostic = next(line for line in events.splitlines() if 'type=dispatch-capture-rejected|' in line)
    assert 'run_id=worker-7-impl-' in diagnostic and '|attempt_id=' in diagnostic
    assert diagnostic.endswith(f'|reason={reason}')
    assert 'feature check' not in diagnostic


@pytest.mark.parametrize('kind', ['nonzero', 'runner-failure', 'runner-nonzero', 'no-runner-record',
                                  'no-durable-exit', 'unknown-shutdown'])
def test_typed_capture_requires_successful_owned_exit(tmp_path, monkeypatch, kind):
    from ai_skills import dispatch

    config = typed_capture_worker(tmp_path, after='\nraise SystemExit(7)\n' if kind == 'nonzero' else '')
    original = dispatch.run_agent

    def invoke(run):
        result = original(run)
        if kind == 'runner-failure':
            raise RuntimeError('injected runner failure')
        if kind == 'runner-nonzero':
            return replace(result, return_code=1)
        if kind == 'no-runner-record':
            Path(run.process_state_file).unlink()
        if kind == 'no-durable-exit':
            path = next(Path(run.record_directory).glob('*.json'))
            record = json.loads(path.read_bytes())
            del record['return_code']
            dispatch.write_json_atomic(path, record)
        if kind == 'unknown-shutdown':
            inspect = dispatch.ProcessSupervisor.inspect

            def unknown_shutdown(supervisor, identity):
                liveness = inspect(supervisor, identity)
                assert liveness.state == 'dead'
                return replace(liveness, state='unknown')

            monkeypatch.setattr(dispatch.ProcessSupervisor, 'inspect', unknown_shutdown)
        return result

    monkeypatch.setattr(dispatch, 'run_agent', invoke)
    result = dispatch_worker(config)
    captured_reason = json.loads(read_typed_capture(config))['result']['reason']
    assert not config.agent_run.result_file.exists()
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    if kind in ('no-durable-exit', 'unknown-shutdown'):
        reason = 'provider-exit-status-unknown' if kind == 'no-durable-exit' else 'shutdown-identity-unknown'
        assert (result.state, result.reason, outcome['status']) == ('unknown', reason, 'pending')
        assert not config.agent_run.done_file.exists()
    else:
        assert result.state == outcome['status'] == 'failed'
        assert outcome['reason'] != captured_reason
        assert outcome['provider_return_code'] == (7 if kind == 'nonzero' else 0)
        assert '|status=failed|' in config.agent_run.done_file.read_text(encoding='utf-8')


def record_stop_after_provider_exit(monkeypatch, config, reason):
    from ai_skills import dispatch

    original = dispatch.run_agent

    def invoke(run):
        result = original(run)
        assert result.return_code == 0
        dispatch._record_stop_intent(replace(config, agent_run=run), reason)
        return result

    monkeypatch.setattr(dispatch, 'run_agent', invoke)


def assert_forced_stop(config, result, reason, status, code):
    assert (result.state, result.reason, result.return_code) == (status, reason, code)
    state = json.loads(config.state_file.read_bytes())
    assert (state['state'], state['reason'], state['return_code']) == (status, reason, code)
    assert state['stop_intent']['reason'] == reason
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert (outcome['status'], outcome['reason'], outcome['worker_return_code']) == (status, reason, code)
    assert outcome['provider_return_code'] == 0
    assert f'|status={status}|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')


@pytest.mark.parametrize(('reason', 'status', 'code'), [
    ('hard-limit-exceeded', 'failed', 124), ('dispatch-timeout', 'failed', 124),
    ('interrupted', 'interrupted', 130),
])
def test_forced_stop_prevents_typed_capture_import(tmp_path, monkeypatch, reason, status, code):
    config = typed_capture_worker(tmp_path)
    record_stop_after_provider_exit(monkeypatch, config, reason)

    result = dispatch_worker(config)

    read_typed_capture(config)
    assert not config.agent_run.result_file.exists()
    assert_forced_stop(config, result, reason, status, code)


def test_direct_interrupted_result_survives_forced_stop(tmp_path, monkeypatch):
    config = typed_capture_worker(tmp_path, after='''
direct = dict(report, status='interrupted', summary='Interrupted before verification completed.',
              evidence=['partial work preserved'])
payload = result_path.with_name('direct-payload.json')
payload.write_text(json.dumps(direct), encoding='utf-8')
subprocess.run([sys.executable, '-m', 'ai_skills', 'artifact', 'write-json', '--role', direct['role'],
                '--issue', direct['issue'], '--payload-file', str(payload), '--result-file', str(result_path)],
               check=True)
result_path.with_name('direct-original').write_bytes(result_path.read_bytes())
''')
    record_stop_after_provider_exit(monkeypatch, config, 'hard-limit-exceeded')

    result = dispatch_worker(config)

    direct = config.agent_run.result_file.read_bytes()
    assert direct == (tmp_path / 'direct-original').read_bytes()
    assert json.loads(direct)['status'] == 'interrupted'
    assert json.loads(direct)['evidence'] == ['partial work preserved']
    assert json.loads(direct)['reason'] == 'artifact-recovery-required'
    assert json.loads(read_typed_capture(config))['result']['status'] == 'failed'
    assert_forced_stop(config, result, 'hard-limit-exceeded', 'failed', 124)


MODIFY_IDS = ['R001', 'R002']


def build_modify_capture(*, no_op=False):
    """Fake-Codex modifier body: optionally commit review fixes, then build the envelope."""
    return f'''
contract = metadata['result_capture']
capture = Path(args[-2])
assert args[-3:] == ['--output-last-message', str(capture), '-']
assert contract['envelope']['role'] == 'modify'
assert record['pre_spawn_head'] != record['issue_base_sha']
assert Path('feature.txt').read_text(encoding='utf-8') == 'implemented'
if not {no_op}:
    Path('feature.txt').write_text('implemented and reviewed', encoding='utf-8')
    Path('review.txt').write_text('review fix', encoding='utf-8')
    subprocess.run(['git', 'add', 'feature.txt', 'review.txt'], check=True)
    subprocess.run(['git', 'commit', '-qm', 'Address review'], check=True)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
closure = [{{'id': comment, 'status': 'addressed', 'action': 'Fixed ' + comment,
             'files_changed': [] if {no_op} else ['feature.txt'], 'verification': ['feature check passed']}}
           for comment in contract['review_comment_ids']]
report = {{'schema_version': 1, 'issue': '7', 'role': 'modify', 'status': 'success',
    'commit_sha': 'NONE' if {no_op} else head, 'files_committed': [] if {no_op} else ['feature.txt', 'review.txt'],
    'review_comment_closure': closure, 'plan_deviations': [],
    'verification': {{'passed': True, 'commit_sha': head, 'commands': ['feature check', 'git check'],
        'checks': [{{'command': check, 'passed': True, 'evidence': 'observed exit 0'}}
                   for check in contract['required_checks']]}}}}
if {no_op}:
    report['no_op'] = True
envelope = {{**contract['envelope'], 'result': report}}
'''


WRITE_CAPTURE = '''
raw = json.dumps(envelope).encode('utf-8')
capture.write_bytes(raw)
result_path.with_name('capture-original').write_bytes(raw)
'''


def modify_worker(tmp_path, body, *, ids=MODIFY_IDS, checks=None, **options):
    """Baseline A, implementation commit B before spawn, modifier admitted at B."""
    from ai_skills.git import git_output

    contract = {'required_checks': list(CAPTURE_CHECKS['required_checks'] if checks is None else checks), 'review_comment_ids': list(ids)}
    config = capture_worker(tmp_path, body, result_capture=contract, **options)
    root = str(config.agent_run.repo_root)
    (config.agent_run.repo_root / 'feature.txt').write_text('implemented', encoding='utf-8')
    git_output(root, 'add', 'feature.txt')
    git_output(root, 'commit', '-qm', 'Implement feature')
    return replace(config, agent_run=config.agent_run.replace(role='modify'),
                   pre_spawn_head=git_output(root, 'rev-parse', 'HEAD'))


FRONTEND_CHECK = 'cd web && bun run build'
SKIP_FRONTEND = """
for check in report['verification']['checks']:
    if check['command'] == %r:
        check.pop('passed')
        check.update(skipped=True, evidence='path-scoped: no changed file under web/**')
""" % FRONTEND_CHECK


def scoped_modify_worker(tmp_path, body, paths):
    """Like modify_worker, with a check-scopes map committed in the issue base."""
    from ai_skills.git import git_output

    contract = {'required_checks': ['feature check', FRONTEND_CHECK], 'review_comment_ids': list(MODIFY_IDS)}
    config = capture_worker(tmp_path, body, result_capture=contract)
    root = config.agent_run.repo_root
    scopes = {'schema_version': 1, 'scopes': [{'check_prefixes': ['cd web && '], 'paths': paths}]}
    (root / '.ai-skills-check-scopes.json').write_text(json.dumps(scopes), encoding='utf-8')
    git_output(str(root), 'add', '.ai-skills-check-scopes.json')
    git_output(str(root), 'commit', '-qm', 'Declare check scopes')
    base = git_output(str(root), 'rev-parse', 'HEAD')
    (root / 'feature.txt').write_text('implemented', encoding='utf-8')
    git_output(str(root), 'add', 'feature.txt')
    git_output(str(root), 'commit', '-qm', 'Implement feature')
    return replace(config, agent_run=config.agent_run.replace(role='modify'), issue_base_sha=base,
                   pre_spawn_head=git_output(str(root), 'rev-parse', 'HEAD'))


def test_captured_modifier_may_skip_checks_outside_changed_paths(tmp_path):
    config = scoped_modify_worker(tmp_path, build_modify_capture() + SKIP_FRONTEND + WRITE_CAPTURE, ['web/**'])
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    checks = json.loads(config.agent_run.result_file.read_bytes())['verification']['checks']
    assert [check.get('skipped', False) for check in checks] == [False, True]


def test_captured_modifier_skip_is_rejected_when_change_touches_scope(tmp_path):
    config = scoped_modify_worker(tmp_path, build_modify_capture() + SKIP_FRONTEND + WRITE_CAPTURE, ['*.txt'])
    assert dispatch_worker(config).state != 'completed'
    assert 'reason=verification-check-skip-not-allowed' in config.agent_run.events_log.read_text(encoding='utf-8')


def test_fake_codex_captured_modifier_commit_preserves_closure_and_issue_baseline(tmp_path):
    from ai_skills.dispatch import adopt_worker
    from ai_skills.git import current_head, git_output

    config = modify_worker(tmp_path, build_modify_capture() + '''
assert contract['review_comment_ids'] == ['R001', 'R002']
''' + WRITE_CAPTURE)
    root, base, start = str(config.agent_run.repo_root), config.issue_base_sha, config.pre_spawn_head
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report == json.loads(capture.read_bytes())['result']
    assert [entry['id'] for entry in report['review_comment_closure']] == MODIFY_IDS
    assert report['commit_sha'] == current_head(root) != start
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['status'] == 'success'
    assert outcome['git_progress'] == {'head': report['commit_sha'], 'pre_spawn_head': start, 'issue_base_sha': base}
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert (saved['issue_base_sha'], saved['pre_spawn_head']) == (base, start)
    assert saved['result_capture']['review_comment_ids'] == MODIFY_IDS
    assert git_output(root, 'rev-list', '--count', f'{base}..HEAD') == '2'
    assert git_output(root, 'diff', '--name-only', f'{base}..HEAD').split() == ['feature.txt', 'review.txt']
    assert '|status=success|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    assert adopt_worker(config.state_file).state == 'completed'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


DECLINE_SECOND = '''
closure[1].update(status='declined', action='Declined: the suggested guard duplicates an existing check',
                  files_changed=[], verification=['feature.txt line 1 already enforces the guard'])
report['reviewer_notes'] = 'compatible extra field'
'''


def test_file_modifier_noop_preserves_reviewed_head_without_verification_sha(tmp_path):
    from ai_skills.dispatch import adopt_worker
    from ai_skills.git import current_head, git_output

    config = modify_worker(tmp_path, f'''
assert 'result_capture' not in metadata
report = {{'schema_version': 1, 'issue': '7', 'role': 'modify', 'status': 'success',
    'no_op': True, 'commit_sha': 'NONE', 'files_committed': [],
    'review_comment_closure': [{{'id': comment, 'status': 'addressed',
        'action': 'Already fixed', 'files_changed': [], 'verification': ['feature check passed']}}
        for comment in {MODIFY_IDS!r}],
    'verification': {{'passed': True, 'commands': ['feature check']}}}}
result_path.write_text(json.dumps(report), encoding='utf-8')
''')
    config = replace(config, result_capture=None)
    root, base, reviewed = str(config.agent_run.repo_root), config.issue_base_sha, config.pre_spawn_head
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    assert adopt_worker(config.state_file).state == 'completed'
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report['no_op'] is True and report['commit_sha'] == 'NONE'
    assert 'commit_sha' not in report['verification']
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['git_progress'] == {'head': reviewed, 'pre_spawn_head': reviewed, 'issue_base_sha': base}
    assert current_head(root) == reviewed
    assert git_output(root, 'rev-list', '--count', f'{base}..{reviewed}') == '1'


@pytest.mark.parametrize('no_op', [False, True], ids=['commit', 'no-op'])
def test_captured_modifier_success_preserves_evidenced_declines(tmp_path, no_op):
    from ai_skills.git import current_head, git_output

    config = modify_worker(tmp_path, build_modify_capture(no_op=no_op) + DECLINE_SECOND + WRITE_CAPTURE)
    root, start = str(config.agent_run.repo_root), config.pre_spawn_head
    count = git_output(root, 'rev-list', '--count', 'HEAD')
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert config.agent_run.result_file.with_name('capture-original').read_bytes()
    assert report == json.loads((tmp_path / 'capture-original').read_bytes())['result']
    assert [(entry['id'], entry['status']) for entry in report['review_comment_closure']] == [
        ('R001', 'addressed'), ('R002', 'declined')]
    assert report['review_comment_closure'][1]['verification'] == ['feature.txt line 1 already enforces the guard']
    assert report['reviewer_notes'] == 'compatible extra field'
    if no_op:
        assert (report['commit_sha'], report['files_committed']) == ('NONE', [])
        assert report['verification']['commit_sha'] == current_head(root) == start
        assert git_output(root, 'rev-list', '--count', 'HEAD') == count
    else:
        assert report['commit_sha'] == current_head(root) != start
    assert not git_output(root, 'status', '--porcelain')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize(('mutation', 'reason'), [
    ("closure.pop()", 'review-comment-closure-incomplete'),
    ("closure.clear()", 'review-comment-closure-incomplete'),
    ("closure[1] = dict(closure[0])", 'review-comment-closure-duplicate-id'),
    ("closure.append({**closure[0], 'id': 'R003'})", 'review-comment-closure-unexpected-id'),
    ("closure[1]['id'] = 'R000'", 'review-comment-closure-unexpected-id'),
    ("closure[0]['id'] = ' R001'", 'review-comment-closure-unexpected-id'),
    ("closure[0]['id'] = 1", 'invalid-review-comment-closure'),
    ("closure[0]['status'] = 'blocked'", 'review-comment-unresolved'),
    ("closure[0]['status'] = 'done'", 'review-comment-unresolved'),
    ("closure[0].pop('status')", 'review-comment-unresolved'),
    ("closure[0]['action'] = ' '", 'review-comment-closure-action-missing'),
    ("closure[0].pop('action')", 'review-comment-closure-action-missing'),
    ("closure[0]['files_changed'] = None", 'invalid-review-comment-closure'),
    ("closure[0]['files_changed'] = [1]", 'invalid-review-comment-closure'),
    ("closure[0]['files_changed'] = 'feature.txt'", 'invalid-review-comment-closure'),
    ("closure[0]['verification'] = []", 'review-comment-closure-evidence-missing'),
    ("closure[0]['verification'] = [' ']", 'review-comment-closure-evidence-missing'),
    ("closure[0]['verification'] = 'feature check passed'", 'review-comment-closure-evidence-missing'),
    ("closure[1].update(status='declined', verification=[])", 'review-comment-closure-evidence-missing'),
    ("closure[0] = 'R001'", 'invalid-review-comment-closure'),
    ("report['review_comment_closure'] = {'R001': closure[0]}", 'invalid-review-comment-closure'),
    ("report.pop('review_comment_closure')", 'invalid-review-comment-closure'),
    ("envelope['cycle'] = '3'", 'capture-identity-mismatch'),
    ("envelope['cycle'] = 4", 'capture-identity-mismatch'),
    ("envelope['role'] = 'impl'", 'capture-identity-mismatch'),
    ("report['role'] = 'impl'", 'invalid-result-artifact'),
    ("report['commit_sha'] = record['pre_spawn_head']", 'capture-commit-mismatch'),
    ("report['commit_sha'] = record['issue_base_sha']", 'capture-commit-mismatch'),
    ("Path('feature.txt').write_text('dirty')", 'capture-dirty-worktree'),
    ("Path('untracked').touch()", 'capture-dirty-worktree'),
    ("report['verification']['commit_sha'] = record['pre_spawn_head']", 'verification-evidence-commit-mismatch'),
    ("report['verification']['checks'].pop()", 'verification-checks-incomplete'),
    ("report['verification']['checks'][0]['passed'] = False", 'verification-check-not-passed'),
])
def test_captured_modifier_rejects_invalid_closure_or_attempt_evidence(tmp_path, mutation, reason):
    config = modify_worker(tmp_path, build_modify_capture() + mutation + '\n' + WRITE_CAPTURE)
    assert_modifier_capture_rejected(config, reason)


@pytest.mark.parametrize('mutation', ['', "closure.append({'id': 'R003', 'status': 'addressed', 'action': 'Fixed', "
                                      "'files_changed': [], 'verification': ['inspected']})"])
def test_captured_modifier_explicit_empty_id_set_requires_empty_closure(tmp_path, mutation):
    config = modify_worker(tmp_path, build_modify_capture() + mutation + '\n' + WRITE_CAPTURE, ids=[])
    if not mutation:
        assert dispatch_worker(config).state == 'completed'
        assert json.loads(config.agent_run.result_file.read_bytes())['review_comment_closure'] == []
    else:
        assert_modifier_capture_rejected(config, 'review-comment-closure-unexpected-id')


def assert_modifier_capture_rejected(config, reason):
    result = dispatch_worker(config)
    assert result.state != 'completed'
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    assert capture.read_bytes() == config.agent_run.result_file.with_name('capture-original').read_bytes()
    if config.agent_run.result_file.exists():
        assert json.loads(config.agent_run.result_file.read_bytes())['status'] == 'artifact_recovery_required'
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['status'] != 'success'
    assert '|status=success|' not in config.agent_run.done_file.read_text(encoding='utf-8')
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    diagnostic = next(line for line in events.splitlines() if 'type=dispatch-capture-rejected|' in line)
    assert diagnostic.endswith(f'|reason={reason}') and 'feature check passed' not in diagnostic


MODIFY_BLOCKED = {
    'status': 'blocked', 'reason': 'review-fixture-service-unavailable',
    'summary': 'Blocker: the fixture service refused connections, so R001 cannot be verified. '
               'Smallest next action: restart the local fixture service and rerun the modifier.',
    'evidence': ['feature check: connection refused on localhost:5432', 'partial R001 edit left in feature.txt'],
    'review_comment_closure': [{'id': 'R001', 'status': 'blocked', 'action': 'Awaiting fixture service',
                                'files_changed': ['feature.txt'], 'verification': ['connection refused']}],
}


def blocked_modifier_worker(tmp_path, after=''):
    return modify_worker(tmp_path, BUILD_TYPED_CAPTURE + f'''
report.update({MODIFY_BLOCKED!r})
Path('feature.txt').write_text('partial review fix', encoding='utf-8')
''' + WRITE_CAPTURE + after)


def test_captured_modifier_blocked_preserves_next_action_and_cycle(tmp_path):
    from ai_skills.dispatch import adopt_worker
    from ai_skills.git import current_head, git_output

    config = blocked_modifier_worker(tmp_path)
    root, start = str(config.agent_run.repo_root), config.pre_spawn_head
    result = dispatch_worker(config)
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report == json.loads((tmp_path / 'capture-original').read_bytes())['result']
    assert {key: report[key] for key in MODIFY_BLOCKED} == MODIFY_BLOCKED
    assert (result.state, result.reason, result.return_code) == ('failed', MODIFY_BLOCKED['reason'], 1)
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert (outcome['status'], outcome['reason']) == ('blocked', MODIFY_BLOCKED['reason'])
    done = config.agent_run.done_file.read_text(encoding='utf-8')
    assert '|status=blocked|source=dispatcher|' in done
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert (saved['agent_run']['cycle'], saved['issue_base_sha'], saved['pre_spawn_head']) == (
        4, config.issue_base_sha, start)
    assert current_head(root) == start
    assert git_output(root, 'status', '--porcelain') == 'M feature.txt'
    assert adopt_worker(config.state_file).state == result.state
    assert config.agent_run.done_file.read_text(encoding='utf-8') == done
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('kind', ['nonzero', 'stop', 'direct-file'])
def test_captured_modifier_blocked_respects_exit_stop_and_file_precedence(tmp_path, monkeypatch, kind):
    after = {'nonzero': '\nraise SystemExit(7)\n', 'stop': '', 'direct-file': '''
result_path.write_text(json.dumps(dict(report, summary='Direct blocked report.')), encoding='utf-8')
'''}[kind]
    config = blocked_modifier_worker(tmp_path, after)
    if kind == 'stop':
        record_stop_after_provider_exit(monkeypatch, config, 'hard-limit-exceeded')
    result = dispatch_worker(config)
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    assert capture.read_bytes() == (tmp_path / 'capture-original').read_bytes()
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    if kind == 'direct-file':
        assert json.loads(config.agent_run.result_file.read_bytes())['summary'] == 'Direct blocked report.'
        assert outcome['status'] == 'blocked'
    else:
        # Existing dirty-tree salvage may still run; the captured report is never imported.
        if config.agent_run.result_file.exists():
            assert json.loads(config.agent_run.result_file.read_bytes())['status'] == 'artifact_recovery_required'
        assert outcome['status'] == 'failed' and outcome['reason'] != MODIFY_BLOCKED['reason']
        assert result.state != 'completed'
        if kind == 'stop':
            assert json.loads(config.state_file.read_bytes())['stop_intent']['reason'] == 'hard-limit-exceeded'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


STREAM_SOURCES = {'claude': 'claude-json', 'agy': 'agy-stream-json', 'opencode': 'opencode-json-v1.16.2'}


def stream_capture_worker(tmp_path, monkeypatch, agent, body, *, role='impl', help_text=None, version='1.16.2', build=None, model='', **modify_options):
    from ai_skills import runner

    invocation = {
        'claude': "assert args[-3:] == ['--print', '--output-format', 'json']",
        'agy': "assert args[-4:] == ['--input-format', 'stream-json', '--output-format', 'stream-json']",
        'opencode': "assert args[0] == 'run' and args[-2:] == ['--format', 'json']",
    }[agent]
    build = build if build is not None else (build_modify_capture() if role == 'modify' else BUILD_CAPTURE)
    codex_args = "capture = Path(args[-2])\nassert args[-3:] == ['--output-last-message', str(capture), '-']"
    build = build.replace(codex_args, invocation)
    flags = {'claude': '    --output-format=stream-json)\n--output-format (choices: "text", "json", "stream-json")',
             'agy': '--model\n--input-format\n--output-format', 'opencode': '--format [choices: "default", "json"]'}[agent]
    probe = f"""
if '--version' in sys.argv:
    print({version!r})
    raise SystemExit(0)
if '--help' in sys.argv:
    assert sys.stdin.buffer.read() == b''
    print({(flags if help_text is None else help_text)!r})
    raise SystemExit(0)
"""
    config = (modify_worker(tmp_path, build + body, probe=probe, **modify_options) if role == 'modify' else
              capture_worker(tmp_path, build + body, probe=probe, result_capture=CAPTURE_CHECKS))
    script = config.agent_run.repo_root / 'exec'
    source = script.read_text(encoding='utf-8')
    if agent == 'agy':
        source = source.replace('metadata = json.loads(prompt.decode', "prompt = json.loads(prompt)['message']['content'].encode('utf-8')\nmetadata = json.loads(prompt.decode")
    script.write_text(source, encoding='utf-8')
    registry = json.loads(config.agent_run.registry_file.read_bytes())
    registry['agents']['claude']['invocation']['command'] = 'claude'
    registry['agents']['claude']['invocation']['args_template'].remove('exec')
    registry['agents']['opencode']['user_model_configuration']['config_paths'] = [str(tmp_path / 'opencode.json')]
    (tmp_path / 'opencode.json').write_text('{}', encoding='utf-8')
    config.agent_run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    resolve = runner.resolve_executable

    def fake_executable(argv, environment):
        if argv[0] == agent:
            return (sys.executable, str(script), *argv[1:])
        return resolve(argv, environment)

    monkeypatch.setattr(runner, 'resolve_executable', fake_executable)
    return replace(config, agent_run=config.agent_run.replace(agent=agent, model=model))


CLAUDE_CAPTURE = """
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'result': json.dumps(envelope)}))
"""


AGY_CAPTURE = '''
for event in [
    {'event': 'init', 'conversation_id': 'resumed', 'init': {}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 20,
        'step_type': 'user_input', 'state': 'DONE'}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 21,
        'step_type': 'tool', 'state': 'DONE', 'text_delta': json.dumps(envelope)}},
    {'event': 'result', 'result': {'conversation_id': 'resumed', 'status': 'SUCCESS',
        'num_turns': 7, 'response': json.dumps(envelope)}},
]:
    print(json.dumps(event))
'''


def opencode_capture(mutation=''):
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/opencode/v1.16.2-tool-to-final.json'
    events = json.loads(fixture.read_bytes())['events']
    return f"""
events = {events!r}
decoy = {{**envelope, 'result': {{**report, 'provider_decoy': True}}}}
events[1]['part']['text'] = json.dumps(decoy)
events[2]['part']['state']['output'] = json.dumps(decoy)
events[-2]['part']['text'] = json.dumps(envelope)
{mutation}
for event in events:
    print(json.dumps(event))
"""


@pytest.mark.parametrize('agent', ['codex', 'claude', 'agy', 'opencode'])
@pytest.mark.parametrize('role', ['impl', 'modify'])
def test_omitted_result_handoff(tmp_path, monkeypatch, agent, role):
    from ai_skills.git import current_head, git_output

    omitted = """
assert not result_path.exists()
result_path.with_name('original-envelope').write_text(json.dumps(envelope), encoding='utf-8')
"""
    if agent == 'codex':
        write = "capture.write_text(json.dumps(envelope), encoding='utf-8')\n" + omitted
        config = (modify_worker(tmp_path, build_modify_capture() + write) if role == 'modify' else
                  capture_worker(tmp_path, BUILD_CAPTURE + write, result_capture=CAPTURE_CHECKS))
    else:
        wire = {'claude': CLAUDE_CAPTURE, 'agy': AGY_CAPTURE, 'opencode': opencode_capture()}[agent]
        config = stream_capture_worker(tmp_path, monkeypatch, agent, wire + omitted, role=role)
    root = str(config.agent_run.repo_root)
    start = current_head(root)
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    context = saved['agent_run']['result_capture_context']
    assert context['source'] == STREAM_SOURCES.get(agent, 'codex-last-message')
    assert (saved['agent_run']['final_response_file'] is None) == (agent != 'codex')
    original = json.loads((tmp_path / 'original-envelope').read_bytes())
    assert context['envelope'] == {key: original[key] for key in context['envelope']}
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report == original['result']
    assert report['commit_sha'] == current_head(root) != start
    assert report['verification']['commit_sha'] == report['commit_sha']
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['git_progress'] == {'head': report['commit_sha'], 'pre_spawn_head': start,
                                       'issue_base_sha': config.issue_base_sha}
    assert (outcome['run_id'], outcome['attempt_id']) == (original['run_id'], original['attempt_id'])
    if role == 'modify':
        assert [entry['id'] for entry in report['review_comment_closure']] == MODIFY_IDS
        assert git_output(root, 'rev-list', '--count', f'{config.issue_base_sha}..HEAD') == '2'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize(('agent', 'wire', 'diagnostic'), [
    ('claude', CLAUDE_CAPTURE * 2, 'capture-response-invalid'),
    ('claude', 'print(json.dumps([envelope]))', 'capture-response-invalid'),
    ('claude', CLAUDE_CAPTURE.replace("'is_error': False", "'is_error': True"), 'capture-provider-error'),
    ('claude', "print('{')", 'capture-response-invalid'),
    ('claude', CLAUDE_CAPTURE.replace('json.dumps(envelope)', 'json.dumps({**envelope, "attempt_id": "stale"})'),
     'capture-identity-mismatch'),
    ('agy', AGY_CAPTURE.replace("'status': 'SUCCESS'", "'status': 'FAILED'"), 'capture-provider-error'),
    ('agy', AGY_CAPTURE.replace("'result': {'conversation_id': 'resumed'", "'result': {'conversation_id': 'other'"),
     'capture-response-invalid'),
    ('agy', AGY_CAPTURE.replace("'step_type': 'tool'", "'step_type': 'user_input'"), 'capture-response-invalid'),
    ('agy', AGY_CAPTURE + "print(json.dumps(event))", 'capture-response-invalid'),
    ('opencode', opencode_capture('events.pop()'), 'capture-response-invalid'),
    ('opencode', opencode_capture('events.pop(3)'), 'capture-response-invalid'),
    ('opencode', opencode_capture('events.pop(-2)'), 'capture-response-invalid'),
    ('opencode', opencode_capture("events.insert(-1, events[-2])"), 'capture-response-invalid'),
    ('opencode', opencode_capture("events[-2]['part']['messageID'] = 'another'"), 'capture-response-invalid'),
    ('opencode', opencode_capture("events[-2]['sessionID'] = 'another'"), 'capture-response-invalid'),
    ('opencode', opencode_capture("events.append({'type': 'error', 'sessionID': 'ses_fixture', 'error': 'failed'})"),
     'capture-provider-error'),
])
def test_stream_capture_rejections_preserve_existing_handling(tmp_path, monkeypatch, agent, wire, diagnostic):
    from ai_skills.git import current_head

    config = stream_capture_worker(tmp_path, monkeypatch, agent, wire)
    root = str(config.agent_run.repo_root)
    result = dispatch_worker(config)
    assert result.state == 'artifact-recovery-required', result.reason
    assert current_head(root) != config.issue_base_sha
    salvage = json.loads(config.agent_run.result_file.read_bytes())
    assert salvage['status'] == 'artifact_recovery_required'
    assert salvage['verification']['passed'] is False
    assert 'reason=' + diagnostic in config.agent_run.events_log.read_text(encoding='utf-8')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('binding', ['runner-path', 'supervisor-path', 'runner-record', 'replaced', 'hardlink', 'symlink'])
def test_stream_capture_requires_original_stdout(tmp_path, monkeypatch, binding):
    from ai_skills import dispatch

    if os.name == 'nt' and binding == 'symlink':
        pytest.skip('Windows symlink privilege varies')
    config = stream_capture_worker(tmp_path, monkeypatch, 'claude', CLAUDE_CAPTURE)
    finalize = dispatch._finalize_attempt

    def change_binding(admitted, **options):
        run = admitted.agent_run
        path = Path(run.stream_directory) / 'stdout'
        record = Path(run.process_state_file)
        if binding == 'runner-record':
            record.unlink()
        elif binding == 'runner-path':
            process = json.loads(record.read_bytes())
            process['stdout_file'] = str(path.with_name('stderr'))
            record.write_text(json.dumps(process), encoding='utf-8')
        elif binding == 'supervisor-path':
            supervisor_record, = run.record_directory.glob('*.json')
            process = json.loads(supervisor_record.read_bytes())
            process['spec']['stdout'] = str(path.with_name('stderr'))
            supervisor_record.write_text(json.dumps(process), encoding='utf-8')
        elif binding == 'hardlink':
            os.link(path, path.with_name('alias'))
        else:
            original = path.rename(path.with_name('original'))
            if binding == 'symlink':
                path.symlink_to(original)
            else:
                path.write_bytes(original.read_bytes())
        return finalize(admitted, **options)

    monkeypatch.setattr(dispatch, '_finalize_attempt', change_binding)
    assert dispatch_worker(config).state == 'artifact-recovery-required'
    salvage = json.loads(config.agent_run.result_file.read_bytes())
    assert salvage['status'] == 'artifact_recovery_required'
    assert salvage['verification']['passed'] is False
    diagnostic = 'capture-runner-exit-unconfirmed' if binding == 'runner-record' else 'capture-path-unsafe'
    assert 'reason=' + diagnostic in config.agent_run.events_log.read_text(encoding='utf-8')


@pytest.mark.parametrize('direct', ['invalid', 'valid'])
def test_stream_capture_preserves_direct_result_file(tmp_path, monkeypatch, direct):
    write = "result_path.write_bytes(b'')" if direct == 'invalid' else (
        "result_path.write_text(json.dumps({**report, 'publication': 'direct'}), encoding='utf-8')")
    config = stream_capture_worker(tmp_path, monkeypatch, 'claude', CLAUDE_CAPTURE + write + '\n')
    result = dispatch_worker(config)
    raw = config.agent_run.result_file.read_bytes()
    if direct == 'valid':
        assert result.state == 'completed'
        assert json.loads(raw)['publication'] == 'direct'
    else:
        assert result.state != 'completed'
        assert raw == b''


GEMINI_4 = 'gemini-4-neon-high'
AGY_MODEL_ASSERTION = ("assert args.count('--model') == 1\n"
                       f"assert args[args.index('--model') + 1] == {GEMINI_4!r}\n")
AGY_STEPS_ONLY = AGY_CAPTURE.split("    {'event': 'result'", 1)[0] + "]:\n    print(json.dumps(event))\n"
AGY_MALFORMED_TERMINAL = AGY_CAPTURE.replace("'response': json.dumps(envelope)", "'response': '{'")
# Real AGY streams: progress narration in earlier assistant messages, a recovered
# tool ERROR step, and result.response concatenating every assistant message.
AGY_NARRATED_CAPTURE = '''
narration = 'Running RED tests; waiting for completion.\\n'
for event in [
    {'event': 'init', 'conversation_id': 'resumed', 'init': {}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 20,
        'step_type': 'user_input', 'state': 'DONE'}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 21,
        'step_type': 'agent_response', 'state': 'DONE', 'text_delta': narration}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 22,
        'step_type': 'tool', 'state': 'ERROR', 'tool_name': 'read_file'}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 23,
        'step_type': 'agent_response', 'state': 'ACTIVE', 'text_delta': json.dumps(envelope)}},
    {'event': 'step_update', 'step_update': {'conversation_id': 'resumed', 'step_index': 23,
        'step_type': 'agent_response', 'state': 'DONE'}},
    {'event': 'result', 'result': {'conversation_id': 'resumed', 'status': 'SUCCESS',
        'num_turns': 7, 'response': narration + json.dumps(envelope)}},
]:
    print(json.dumps(event))
'''
AGY_NARRATED_FINAL_PROSE = AGY_NARRATED_CAPTURE.replace(
    "'text_delta': json.dumps(envelope)}", "'text_delta': 'Done. ' + json.dumps(envelope)}").replace(
    "narration + json.dumps(envelope)", "narration + 'Done. ' + json.dumps(envelope)")
AGY_UNVERIFIED = BUILD_CAPTURE.replace("'passed': True, 'evidence': 'observed exit 0'",
                                       "'passed': False, 'evidence': 'observed exit 1'")


def agy_gemini_worker(tmp_path, monkeypatch, wire, build=None, *, role='impl', **options):
    return stream_capture_worker(tmp_path, monkeypatch, 'agy', AGY_MODEL_ASSERTION + wire,
                                 build=build, model=GEMINI_4, role=role, **options)


def fresh_modify_attempt(config, directory, *, cycle=4, head=None):
    directory.mkdir()
    run = config.agent_run.replace(cycle=cycle, result_file=directory / 'result.json',
        done_file=directory / 'worker.done', log_file=directory / 'worker.log',
        run_id='', attempt_id='')
    return replace(config, agent_run=run, state_file=directory / 'worker.state.json',
                   pre_spawn_head=head or config.pre_spawn_head)


def test_agy_gemini_4_modify_retries_missing_terminal_in_same_cycle(tmp_path, monkeypatch):
    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_STEPS_ONLY,
                               build=build_modify_capture(no_op=True), role='modify')
    first = dispatch_worker(config)
    assert first.state == 'failed'
    assert not config.agent_run.result_file.exists()
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    raw = Path(saved['agent_run']['stream_directory']) / 'stdout'
    outcome = Path(str(config.state_file) + '.outcome.json')
    originals = [p.read_bytes() for p in (raw, outcome)]
    assert json.loads(originals[1])['status'] == 'failed'
    assert 'reason=capture-response-invalid' in config.agent_run.events_log.read_text()
    script = config.agent_run.repo_root / 'exec'
    script.write_text(script.read_text().replace(AGY_STEPS_ONLY, AGY_CAPTURE))
    retry = fresh_modify_attempt(config, tmp_path / 'retry')
    result = dispatch_worker(retry)
    assert result.state == 'completed', result.reason
    retry_saved = json.loads(Path(str(retry.state_file) + '.config.json').read_bytes())
    assert retry_saved['agent_run']['attempt_id'] != saved['agent_run']['attempt_id']
    assert retry_saved['agent_run']['cycle'] == saved['agent_run']['cycle'] == 4
    report = json.loads(retry.agent_run.result_file.read_bytes())
    assert [item['id'] for item in report['review_comment_closure']] == MODIFY_IDS
    assert [p.read_bytes() for p in (raw, outcome)] == originals


@pytest.mark.parametrize('replay', ['prior-stream', 'prior-cycle'])
def test_agy_gemini_4_modify_rejects_previous_cycle_capture(tmp_path, monkeypatch, replay):
    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_CAPTURE, role='modify')
    assert dispatch_worker(config).state == 'completed'
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    raw = Path(saved['agent_run']['stream_directory']) / 'stdout'
    paths = [config.agent_run.result_file, raw, Path(str(config.state_file) + '.outcome.json')]
    originals = [path.read_bytes() for path in paths]
    script = config.agent_run.repo_root / 'exec'
    source = script.read_text()
    # Current attempt starts at the committed prior result, without additional Git progress.
    start = source.index('contract = metadata[\'result_capture\']')
    noop = build_modify_capture(no_op=True).replace(
        "capture = Path(args[-2])\nassert args[-3:] == ['--output-last-message', str(capture), '-']",
        "assert args[-4:] == ['--input-format', 'stream-json', '--output-format', 'stream-json']")
    noop = noop.replace("== 'implemented'", "== 'implemented and reviewed'")
    head = json.loads(originals[0])['commit_sha']
    if replay == 'prior-cycle':
        script.write_text(source[:start] + AGY_MODEL_ASSERTION + noop + AGY_CAPTURE)
        valid = fresh_modify_attempt(config, tmp_path / 'valid', cycle=5, head=head)
        assert dispatch_worker(valid).state == 'completed'
        valid_report = json.loads(valid.agent_run.result_file.read_bytes())
        assert valid_report['no_op'] and valid_report['verification']['commit_sha'] == head
    wire = (f"print({originals[1].decode()!r}, end='')\n" if replay == 'prior-stream' else
            noop + "envelope['cycle'] = '4'\n" + AGY_CAPTURE)
    script.write_text(source[:start] + AGY_MODEL_ASSERTION + wire)
    current = fresh_modify_attempt(config, tmp_path / 'current', cycle=5, head=head)
    result = dispatch_worker(current)
    assert result.state != 'completed'
    assert not current.agent_run.result_file.exists()
    current_saved = json.loads(Path(str(current.state_file) + '.config.json').read_bytes())
    events = current.agent_run.events_log.read_text().splitlines()
    assert any('reason=capture-identity-mismatch' in line and
               f"attempt_id={current_saved['agent_run']['attempt_id']}" in line for line in events)
    assert json.loads(Path(str(current.state_file) + '.outcome.json').read_bytes())['status'] == 'failed'
    assert [path.read_bytes() for path in paths] == originals


def test_agy_gemini_4_impl_publishes_verified_success(tmp_path, monkeypatch):
    from ai_skills.git import current_head

    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_CAPTURE)
    root = str(config.agent_run.repo_root)
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report['commit_sha'] == current_head(root) != config.issue_base_sha
    assert report['verification']['commands'] == CAPTURE_CHECKS['required_checks']
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert (saved['agent_run']['agent'], saved['agent_run']['model']) == ('agy', GEMINI_4)
    assert saved['agent_run']['result_capture_context']['source'] == 'agy-stream-json'
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['status'] == 'success' and outcome['git_progress']['head'] == report['commit_sha']
    done = config.agent_run.done_file.read_text(encoding='utf-8')
    assert f'|model={GEMINI_4}|' in done and '|status=success|source=dispatcher|' in done
    assert f'model={GEMINI_4}' in config.agent_run.events_log.read_text(encoding='utf-8')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


def test_agy_narrated_turn_publishes_final_message_envelope(tmp_path, monkeypatch):
    from ai_skills.git import current_head

    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_NARRATED_CAPTURE)
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report['commit_sha'] == current_head(str(config.agent_run.repo_root))
    assert 'dispatch-capture-rejected' not in config.agent_run.events_log.read_text(encoding='utf-8')


def test_agy_final_message_with_prose_is_still_rejected(tmp_path, monkeypatch):
    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_NARRATED_FINAL_PROSE)
    assert dispatch_worker(config).state == 'artifact-recovery-required'
    assert 'reason=capture-invalid-json' in config.agent_run.events_log.read_text(encoding='utf-8')


def test_agy_gemini_4_publishes_result_before_outcome_and_done(tmp_path, monkeypatch):
    from ai_skills import dispatch

    order = []
    for name in ('write_json_atomic', 'write_text_atomic'):
        def record(path, *args, _original=getattr(dispatch, name), **kwargs):
            order.append(Path(path).name)
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(dispatch, name, record)
    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_CAPTURE)
    assert dispatch_worker(config).state == 'completed'
    # The admission journal is pending; only terminal journaling follows the published result.
    terminal = order[order.index('result.json'):]
    assert terminal.index('worker.state.json.outcome.json') < terminal.index('worker.done')


@pytest.mark.parametrize(('wire', 'build', 'diagnostic'), [
    (AGY_STEPS_ONLY, None, 'capture-response-invalid'),
    (AGY_MALFORMED_TERMINAL, None, 'capture-invalid-json'),
    (AGY_CAPTURE, AGY_UNVERIFIED, 'verification-check-not-passed'),
])
def test_agy_gemini_4_impl_failure_retains_raw_capture_and_recovers(tmp_path, monkeypatch, wire, build, diagnostic):
    from ai_skills.git import current_head

    config = agy_gemini_worker(tmp_path, monkeypatch, wire, build=build)
    root = str(config.agent_run.repo_root)
    result = dispatch_worker(config)
    assert result.state == 'artifact-recovery-required', result.reason
    salvage = json.loads(config.agent_run.result_file.read_bytes())
    assert salvage['status'] == 'artifact_recovery_required' and salvage['verification']['passed'] is False
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['status'] == 'failed' and outcome['reason'] == 'artifact-recovery-required'
    assert outcome['git_progress']['head'] == current_head(root)
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    raw = Path(saved['agent_run']['stream_directory'], 'stdout').read_bytes()
    assert b'"event": "init"' in raw and saved['agent_run']['model'] == GEMINI_4
    assert 'reason=' + diagnostic in config.agent_run.events_log.read_text(encoding='utf-8')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('direct', ['invalid', 'valid'])
def test_agy_gemini_4_preserves_direct_result_file(tmp_path, monkeypatch, direct):
    write = "result_path.write_bytes(b'')" if direct == 'invalid' else (
        "result_path.write_text(json.dumps({**report, 'publication': 'direct'}), encoding='utf-8')")
    config = agy_gemini_worker(tmp_path, monkeypatch, AGY_CAPTURE + write + '\n')
    result = dispatch_worker(config)
    raw = config.agent_run.result_file.read_bytes()
    if direct == 'valid':
        assert result.state == 'completed'
        assert json.loads(raw)['publication'] == 'direct'
        assert f'|model={GEMINI_4}|' in config.agent_run.done_file.read_text(encoding='utf-8')
    else:
        assert result.state != 'completed'
        assert raw == b''
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


def agy_managed_role_worker(tmp_path, role, result, *, instructions=None):
    script = f'''import json, os, sys
from pathlib import Path
assert sys.argv[1:3] == ['--model', {('gemini-4-hydrogen-high' if role == 'complexity' else GEMINI_4)!r}]
result = Path(os.environ['RUN_WITH_IT_RESULT_FILE'])
result.write_text(json.dumps({result!r}), encoding='utf-8')
if {role!r} == 'review':
    Path(os.environ['REVIEWER_INSTRUCTIONS_FILE']).write_text(
        json.dumps({instructions!r}), encoding='utf-8')
'''
    config = worker_config(tmp_path, script)
    registry = json.loads(config.agent_run.registry_file.read_text(encoding='utf-8'))
    registry['agents']['agy'] = registry['agents'].pop('fake')
    registry['agents']['agy']['model'] = {'flag_template': '--model {{model}}'}
    registry['agents']['agy']['invocation']['args_template'] = [
        str(tmp_path / 'provider.py'), '{{model_flag}}', '{{prompt}}']
    config.agent_run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    result_file = tmp_path / ('review-cycle-1-status.json' if role == 'review' else 'result.json')
    return replace(config, agent_run=config.agent_run.replace(
        agent='agy', model='gemini-4-hydrogen-high' if role == 'complexity' else GEMINI_4,
        role=role, result_file=result_file))


@pytest.mark.parametrize(('role', 'result', 'instructions', 'valid', 'model'), [
    ('complexity', {'schema_version': 2, 'total': 7, 'level': 'quite-easy', 'scores': {key: 1 for key in
     ('coupling_and_blast_radius', 'pattern_novelty', 'state_and_concurrency_risk',
      'data_and_rollback_risk', 'verification_risk', 'ambiguity_of_requirements', 'scope_size')},
     'rationale': {key: 'reason' for key in ('coupling_and_blast_radius', 'pattern_novelty',
      'state_and_concurrency_risk', 'data_and_rollback_risk', 'verification_risk',
      'ambiguity_of_requirements', 'scope_size')},
     'files_inspected': ['src/example.py']},
     None, True, 'gemini-4-hydrogen-high'),
    ('complexity', {'total': 9}, None, False, 'gemini-4-hydrogen-high'),
    ('review', {'verdict': 'approve', 'comment_count': 0, 'nitpick_only': False},
     {'verdict': 'approve', 'summary': 'No findings.', 'comments': [], 'blocking_reasons': []}, True, GEMINI_4),
    ('review', {'verdict': 'approve', 'comment_count': 1, 'nitpick_only': False},
     {'verdict': 'approve', 'summary': 'No findings.', 'comments': [], 'blocking_reasons': []}, False, GEMINI_4),
    ('artifact-recovery', {'status': 'success', 'artifact_recovered': True}, None, True, GEMINI_4),
    ('artifact-recovery', ['malformed'], None, False, GEMINI_4),
])
def test_managed_agy_phase_dispatch_validates_role_artifacts_and_emits_model(
        tmp_path, monkeypatch, role, result, instructions, valid, model):
    config = agy_managed_role_worker(tmp_path, role, result, instructions=instructions)
    outcome = dispatch_worker(config)
    assert (outcome.state == 'completed') is valid
    events = config.agent_run.events_log.read_text(encoding='utf-8')
    expected = 'dispatch-complete' if valid else 'dispatch-failed'
    assert expected in events
    assert f'agent=agy' in events and f'model={model}' in events
    assert f'role={role}' in events


@pytest.mark.parametrize(('agent', 'options'), [
    ('claude', {'help_text': '--output-format (choices: "text", "stream-json")\n --other json'}),
    ('opencode', {'version': '1.16.1'}),
    ('opencode', {'help_text': '--formatting (choices: "json")'}),
    ('opencode', {'unconfigured': True}),
])
def test_optional_stream_capture_falls_back_to_file_reporting(tmp_path, monkeypatch, agent, options):
    options = options.copy()
    unconfigured = options.pop('unconfigured', False)
    config = stream_capture_worker(tmp_path, monkeypatch, agent, '', **options)
    if unconfigured:
        (tmp_path / 'opencode.json').unlink()
    script = config.agent_run.repo_root / 'exec'
    source = script.read_text(encoding='utf-8').split("contract = metadata['result_capture']", 1)[0]
    script.write_text(source + """
assert 'result_capture' not in metadata
assert '--output-format' not in args and '--format' not in args
result_path.write_text(json.dumps({'issue': '7', 'role': 'impl', 'status': 'success',
    'no_op': True, 'commit_sha': 'NONE', 'files_committed': [],
    'verification': {'passed': True, 'commands': ['file check']}}), encoding='utf-8')
""", encoding='utf-8')
    assert dispatch_worker(config).state == 'completed'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


def test_quiet_stream_capture_waits_for_the_original_provider(tmp_path, monkeypatch):
    # Stay silent until the dispatcher reports the stall, so slow runners
    # cannot let the provider finish before silence is observed.
    wait_for_stall = (f"events = Path({str(tmp_path / 'events')!r})\n"
                      "deadline = time.monotonic() + 5\n"
                      "while time.monotonic() < deadline and not (events.exists() and\n"
                      "        'type=worker-stalled|' in events.read_text(encoding='utf-8')):\n"
                      "    time.sleep(.01)\n")
    config = stream_capture_worker(tmp_path, monkeypatch, 'claude', wait_for_stall + CLAUDE_CAPTURE)
    config = replace(config, quiet_seconds=.02, stall_seconds=.05, hard_limit_seconds=10)
    assert dispatch_worker(config).state == 'completed'
    assert 'type=worker-stalled|' in config.agent_run.events_log.read_text(encoding='utf-8')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize('agent', ['codex', 'claude', 'agy', 'opencode'])
def test_modifier_capture_supplies_exact_ids_and_checks_without_findings(tmp_path, monkeypatch, agent):
    from ai_skills.artifacts import valid_review_instructions
    from ai_skills.git import git_output

    ids, checks = ['R003', 'R001'], ['feature check', 'git check', 'review check R003']
    comments = [dict(id=i, file='feature.txt', severity='warning', category='correctness', fix='Fix', evidence='SENTINEL-FINDING', expected_change='Fixed', verification='review check R003', blocking=True, line=1) for i in ids]
    instructions = {'verdict': 'revise', 'summary': 'Review', 'comments': comments, 'blocking_reasons': []}
    assert valid_review_instructions(instructions)
    path = tmp_path / 'instructions.json'
    path.write_text(json.dumps(instructions), encoding='utf-8')
    assertions = f'''
assert contract['review_comment_ids'] == {ids!r}
assert contract['required_checks'] == {checks!r}
assert set(contract) == {{'schema_version', 'source', 'cycle', 'required_checks', 'review_comment_ids', 'envelope'}}
assert 'SENTINEL-FINDING' not in prompt.decode('utf-8')
instructions_path = next(line.split('=', 1)[1] for line in prompt.decode('utf-8').splitlines() if line.startswith('REVIEWER_INSTRUCTIONS_FILE='))
assert [c['id'] for c in json.loads(Path(instructions_path).read_text())['comments']] == contract['review_comment_ids']
'''
    if agent == 'codex':
        config = modify_worker(tmp_path, build_modify_capture() + assertions + WRITE_CAPTURE, ids=ids, checks=checks)
    else:
        wire = {'claude': CLAUDE_CAPTURE, 'agy': AGY_CAPTURE, 'opencode': opencode_capture()}[agent]
        config = stream_capture_worker(tmp_path, monkeypatch, agent, assertions + wire, role='modify', ids=ids, checks=checks)
    config.agent_run.context_file.write_text(f'Context\nREVIEWER_INSTRUCTIONS_FILE={path}\n', encoding='utf-8')
    start, base = config.pre_spawn_head, config.issue_base_sha
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert [c['id'] for c in report['review_comment_closure']] == ids
    assert [c['command'] for c in report['verification']['checks']] == checks
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert saved['result_capture'] == {'required_checks': checks, 'review_comment_ids': ids}
    assert saved['agent_run']['result_capture_context']['source'] == STREAM_SOURCES.get(agent, 'codex-last-message')
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['git_progress'] == {'head': report['commit_sha'], 'pre_spawn_head': start, 'issue_base_sha': base}
    assert git_output(str(config.agent_run.repo_root), 'rev-list', '--count', f'{base}..HEAD') == '2'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize(('agent', 'mode'), [('claude', 'omitted'), ('agy', 'omitted'), ('opencode', 'omitted'), ('claude', 'unsupported')])
def test_modifier_without_capture_uses_file_contract(tmp_path, monkeypatch, agent, mode):
    options = {'help_text': '--output-format (choices: "text", "stream-json")'} if mode == 'unsupported' else {}
    config = stream_capture_worker(tmp_path, monkeypatch, agent, '', role='modify', **options)
    if mode == 'omitted':
        config = replace(config, result_capture=None)
    script = config.agent_run.repo_root / 'exec'
    source = script.read_text(encoding='utf-8').split("contract = metadata['result_capture']", 1)[0]
    script.write_text(source + '''
assert 'result_capture' not in metadata
assert '--format' not in args
Path('review.txt').write_text('review fix', encoding='utf-8')
subprocess.run(['git', 'add', 'review.txt'], check=True)
subprocess.run(['git', 'commit', '-qm', 'Address review'], check=True)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
report = {'schema_version': 1, 'issue': '7', 'role': 'modify', 'status': 'success',
    'commit_sha': head, 'files_committed': ['feature.txt', 'review.txt'],
    'review_comment_closure': [{'id': i, 'status': 'addressed', 'action': 'Fixed', 'files_changed': ['review.txt'], 'verification': ['checked']} for i in ['R001', 'R002']],
    'verification': {'passed': True, 'commands': ['feature check']}}
result_path.write_text(json.dumps(report), encoding='utf-8')
''', encoding='utf-8')
    start = config.pre_spawn_head
    result = dispatch_worker(config)
    assert result.state == 'completed', result.reason
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert saved['agent_run']['result_capture_context'] is None
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert report['review_comment_closure'][0]['id'] == 'R001'
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert outcome['git_progress']['pre_spawn_head'] == start
    assert outcome['git_progress']['issue_base_sha'] == config.issue_base_sha
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


def test_captured_modifier_blocked_on_stream_adapter_preserves_next_action_and_cycle(tmp_path, monkeypatch):
    from ai_skills.git import current_head, git_output

    build = BUILD_TYPED_CAPTURE.replace("capture = Path(args[-2])\n", '').replace("assert args[-3:] == ['--output-last-message', str(capture), '-']", "assert args[-3:] == ['--print', '--output-format', 'json']")
    body = f"\nreport.update({MODIFY_BLOCKED!r})\nPath('feature.txt').write_text('partial review fix', encoding='utf-8')\n" + CLAUDE_CAPTURE
    config = stream_capture_worker(tmp_path, monkeypatch, 'claude', body, role='modify', build=build)
    start = config.pre_spawn_head
    result = dispatch_worker(config)
    assert (result.state, result.reason, result.return_code) == ('failed', MODIFY_BLOCKED['reason'], 1)
    report = json.loads(config.agent_run.result_file.read_bytes())
    assert {key: report[key] for key in MODIFY_BLOCKED} == MODIFY_BLOCKED
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_bytes())
    assert (outcome['status'], outcome['reason']) == ('blocked', MODIFY_BLOCKED['reason'])
    assert '|status=blocked|source=dispatcher|' in config.agent_run.done_file.read_text(encoding='utf-8')
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_bytes())
    assert (saved['agent_run']['cycle'], saved['issue_base_sha'], saved['pre_spawn_head']) == (4, config.issue_base_sha, start)
    root = str(config.agent_run.repo_root)
    assert current_head(root) == start
    assert git_output(root, 'status', '--porcelain') == 'M feature.txt'


def effort_worker(tmp_path, *, agent='claude', role='impl', effort='', detach=False):
    """Fake provider records its own admitted record and argv before finishing."""
    observed = tmp_path/'observed.json'
    source = f'''import json,os,sys
from pathlib import Path
state=Path(os.environ['RUN_WITH_IT_STATE_FILE'])
record=json.loads(Path(str(state)+'.config.json').read_text())
Path({str(observed)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'effort':record['agent_run']['effort'],
    'requested':record['agent_run'].get('requested_effort','')}}))
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{{}}')
'''
    config = worker_config(tmp_path, source, detach=detach)
    script = tmp_path/'provider.py'
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'agents': {agent: {'model': {'default': 'm'}, 'permission_modes': {'default': ''},
        'invocation': {'command': sys.executable, 'args_template': [str(script), '{{model_settings}}']}}}}))
    run = config.agent_run.replace(agent=agent, role=role, effort=effort, registry_file=registry, issue_dir=tmp_path)
    return replace(config, agent_run=run)


def effort_setting(agent, argv):
    if agent == 'codex':
        return next(item.split('=', 1)[1] for item in argv if item.startswith('model_reasoning_effort='))
    return argv[argv.index('--effort') + 1]


@pytest.mark.parametrize(('agent', 'role', 'effort', 'effective', 'requested'), [
    ('claude', 'impl', '', 'high', ''),
    ('claude', 'impl', 'low', 'low', ''),
    ('claude', 'impl', 'medium', 'medium', ''),
    ('claude', 'impl', 'xhigh', 'high', 'xhigh'),
    ('claude', 'impl', 'maximum', 'high', 'maximum'),
    ('claude', 'plan', '', 'xhigh', ''),
    ('claude', 'plan', 'high', 'high', ''),
    ('claude', 'plan', 'max', 'xhigh', 'max'),
    ('codex', 'impl', 'low', 'high', 'low'),
    ('codex', 'impl', 'xhigh', 'high', 'xhigh'),
    ('codex', 'plan', '', 'xhigh', ''),
    ('codex', 'plan', 'medium', 'xhigh', 'medium'),
])
def test_admission_persists_effective_effort_matching_provider_argv(tmp_path, agent, role, effort, effective, requested):
    config = effort_worker(tmp_path, agent=agent, role=role, effort=effort)
    dispatch_worker(config)
    observed = json.loads((tmp_path/'observed.json').read_text())
    saved = json.loads(Path(str(config.state_file)+'.config.json').read_text())['agent_run']
    assert (saved['effort'], saved.get('requested_effort', '')) == (effective, requested)
    assert (observed['effort'], observed['requested']) == (effective, requested)
    assert effort_setting(agent, observed['argv']) == effective


def test_unknown_effort_is_rejected_before_admission_state_or_provider(tmp_path):
    config = effort_worker(tmp_path, effort='extreme')
    with pytest.raises(ValueError, match='unknown effort'):
        dispatch_worker(config)
    assert not config.state_file.exists()
    assert not Path(str(config.state_file)+'.config.json').exists()
    assert not (tmp_path/'observed.json').exists()


def test_requested_effort_survives_record_round_trip_and_old_records_load(tmp_path):
    config = effort_worker(tmp_path, effort='high')
    adjusted = replace(config, agent_run=config.agent_run.replace(effort='high', requested_effort='xhigh'))
    record = json.loads(json.dumps(adjusted.to_record()))
    assert WorkerConfig.from_record(record).agent_run.requested_effort == 'xhigh'
    del record['agent_run']['requested_effort']
    assert WorkerConfig.from_record(record).agent_run.requested_effort == ''


@pytest.mark.parametrize(('override', 'effective', 'requested'), [
    ('low', 'low', ''),
    ('xhigh', 'high', 'xhigh'),
    (None, 'high', 'maximum'),
])
@pytest.mark.parametrize('flat', [False, True])
def test_saved_config_effort_override_replaces_old_provenance(tmp_path, monkeypatch, override, effective, requested, flat):
    config = effort_worker(tmp_path, agent='claude', role='impl', effort='high')
    saved = replace(config, agent_run=config.agent_run.replace(requested_effort='maximum'))
    config_path = tmp_path/'saved-config.json'
    saved_record = saved.to_record()
    if flat:
        saved_record = {**saved_record['agent_run'], **{k: v for k, v in saved_record.items() if k != 'agent_run'}}
    config_path.write_text(json.dumps(saved_record))
    monkeypatch.setenv('RUN_WITH_IT_DISPATCHER_MANAGED', '0')

    args = ['--config', str(config_path)] + (['--effort', override] if override else [])
    assert dispatch_main(args) in (0, 1)

    record = json.loads(Path(str(config.state_file)+'.config.json').read_text())['agent_run']
    provider = json.loads((tmp_path/'observed.json').read_text())
    assert (record['effort'], record.get('requested_effort', '')) == (effective, requested)
    assert (provider['effort'], provider['requested']) == (effective, requested)
    assert effort_setting('claude', provider['argv']) == effective


@pytest.mark.parametrize(('source', 'role', 'effort', 'expected', 'requested'), [
    ('flat-config', 'impl', 'xhigh', 'high', 'xhigh'),
    ('structured-config', 'plan', 'maximum', 'xhigh', 'maximum'),
    ('environment', 'impl', 'medium', 'medium', ''),
    ('cli', 'plan', 'low', 'low', ''),
])
def test_worker_cli_effort_sources_match_admitted_record_and_provider(tmp_path, monkeypatch,
                                                                      source, role, effort, expected, requested):
    config = effort_worker(tmp_path, agent='claude', role=role, effort='')
    record = config.to_record()
    record['agent_run']['agent'] = 'claude-alias'
    registry = json.loads(config.agent_run.registry_file.read_text())
    registry['aliases'] = {'claude-alias': 'claude'}
    config.agent_run.registry_file.write_text(json.dumps(registry))
    record['agent_run']['registry_file'] = str(config.agent_run.registry_file)
    config_path = tmp_path/'source-config.json'
    args = []
    monkeypatch.setenv('RUN_WITH_IT_DISPATCHER_MANAGED', '0')
    if source == 'flat-config':
        flat = {**record['agent_run'], **{key: value for key, value in record.items() if key != 'agent_run'}}
        flat['effort'] = effort
        flat['detach'] = True
        config_path.write_text(json.dumps(flat))
        args = ['--config', str(config_path)]
    elif source == 'structured-config':
        record['agent_run']['role'] = role
        record['agent_run']['effort'] = effort
        config_path.write_text(json.dumps(record))
        args = ['--config', str(config_path)]
    elif source == 'environment':
        for key, value in {
            'AGENT': 'claude-alias', 'MODEL': 'm', 'MODEL_EFFORT': effort,
            'RUN_WITH_IT_ROLE': role, 'RUN_WITH_IT_ISSUE': '7', 'RUN_WITH_IT_ISSUE_DIR': str(tmp_path),
            'REPO_ROOT': str(tmp_path), 'CONTEXT_PAYLOAD_FILE': str(config.agent_run.context_file),
            'PROMPT_FILE': str(config.agent_run.prompt_file), 'AGENT_REGISTRY_FILE': str(config.agent_run.registry_file),
            'RUN_WITH_IT_STATE_FILE': str(config.state_file), 'RUN_WITH_IT_RESULT_FILE': str(config.agent_run.result_file),
            'RUN_WITH_IT_LOG_FILE': str(config.agent_run.log_file), 'RUN_WITH_IT_DONE_FILE': str(config.agent_run.done_file),
        }.items():
            monkeypatch.setenv(key, value)
    else:
        monkeypatch.setenv('MODEL_EFFORT', 'maximum')
        monkeypatch.delenv('RUN_WITH_IT_ISSUE_DIR', raising=False)
        args = ['--agent', 'claude-alias', '--model', 'm', '--effort', effort, '--role', role,
                '--state-file', str(config.state_file), '--repo-root', str(tmp_path),
                '--context-file', str(config.agent_run.context_file), '--prompt-file', str(config.agent_run.prompt_file),
                '--registry-file', str(config.agent_run.registry_file), '--result-file', str(config.agent_run.result_file),
                '--log-file', str(config.agent_run.log_file), '--done-file', str(config.agent_run.done_file),
                '--issue-dir', str(tmp_path)]

    assert dispatch_main(args) in (0, 1)
    if source == 'flat-config':
        deadline = time.monotonic() + 5
        observed_path = tmp_path/'observed.json'
        while time.monotonic() < deadline and not observed_path.exists():
            time.sleep(.02)
    saved = json.loads(Path(str(config.state_file)+'.config.json').read_text())['agent_run']
    provider = json.loads((tmp_path/'observed.json').read_text())
    assert (saved['effort'], saved.get('requested_effort', '')) == (expected, requested)
    assert (provider['effort'], provider['requested']) == (expected, requested)
    assert effort_setting('claude', provider['argv']) == expected


def test_detached_reattachment_preserves_admitted_effort_record(tmp_path):
    from ai_skills.dispatch import adopt_worker
    config = effort_worker(tmp_path, agent='claude', role='impl', effort='maximum', detach=True)
    dispatch_worker(config)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and json.loads(config.state_file.read_text())['state'] == 'running':
        time.sleep(.03)
    record_path = Path(str(config.state_file)+'.config.json')
    before = record_path.read_bytes()
    observed = json.loads((tmp_path/'observed.json').read_text())
    assert (observed['effort'], observed['requested']) == ('high', 'maximum')
    assert json.loads(before)['agent_run']['effort'] == 'high'
    adopt_worker(config.state_file)
    assert record_path.read_bytes() == before


@pytest.mark.parametrize('agent,extra', [
    ('claude', ('--effort', 'max')),
    ('codex', ('-c', '"model_reasoning_effort"="xhigh"')),
])
def test_raw_effort_config_rejected_before_admission(tmp_path, agent, extra):
    config = effort_worker(tmp_path, agent=agent)
    config = replace(config, agent_run=config.agent_run.replace(extra_args=extra))
    with pytest.raises(ValueError, match='cannot override'):
        dispatch_worker(config)
    assert not config.state_file.exists()
    assert not Path(str(config.state_file) + '.config.json').exists()
    assert not (tmp_path / 'observed.json').exists()


@pytest.mark.parametrize('validate', [False, True])
def test_saved_raw_effort_config_rejected_before_provider(tmp_path, validate):
    config = effort_worker(tmp_path)
    config = replace(config, agent_run=config.agent_run.replace(extra_args=('--effort=max',)))
    path = tmp_path / 'saved.json'
    path.write_text(json.dumps(config.to_record()))
    assert dispatch_main(['--config', str(path)] + (['--validate-only'] if validate else [])) == 2
    assert not (tmp_path / 'observed.json').exists()
    assert not config.state_file.exists()


PLAN_RESULT = {'schema_version': 1, 'issue': '7', 'role': 'plan', 'status': 'success',
               'approach': 'Preserve the canary', 'complexity_level': 'complex',
               'complexity_rationale': 'Cross-module protocol change with read-only capture invariants.',
               'slices': [{'id': 1}]}
PLAN_MARKDOWN = '# Preserve the canary\n\n- Read source and preserve café labels.\n'
PLAN_INVOCATION = ['--fixture-read-only', '--model', GEMINI_4,
                   '--input-format', 'stream-json', '--output-format', 'stream-json']
PLAN_BODY = f"""
assert args == {PLAN_INVOCATION!r}
root = Path.cwd()
assert (root / 'canary').read_text() == 'original'

def reject_fixture_mutations(event, operands):
    if event == 'subprocess.Popen' or (
            event == 'open' and operands[2] & (os.O_WRONLY | os.O_RDWR)):
        raise PermissionError('fixture read-only enforcement')

sys.addaudithook(reject_fixture_mutations)
for mutation in (lambda: (root / 'canary').write_text('changed'),
                 lambda: subprocess.run(['git', 'commit', '--allow-empty', '-m', 'unexpected'])):
    try:
        mutation()
    except PermissionError:
        pass
    else:
        raise AssertionError('fixture mutation was allowed')
envelope = {{**metadata['result_capture']['envelope'],
            'result': {PLAN_RESULT!r}, 'plan_markdown': {PLAN_MARKDOWN!r}}}
"""


def protected_agy_plan_worker(tmp_path, monkeypatch, *, mutation='', wire=AGY_CAPTURE):
    from ai_skills import git, runner

    config = capture_worker(tmp_path, PLAN_BODY + mutation + '\n' + wire)
    run = config.agent_run
    root = run.repo_root
    script = tmp_path / 'provider.py'
    (root / 'exec').replace(script)
    source = script.read_text(encoding='utf-8').replace(
        "metadata = json.loads(prompt.decode",
        "prompt = json.loads(prompt)['message']['content'].encode('utf-8')\nmetadata = json.loads(prompt.decode")
    script.write_text(source, encoding='utf-8')
    (root / 'canary').write_text('original', encoding='utf-8')
    git.git_output(str(root), 'add', 'canary')
    git.git_output(str(root), 'commit', '-qm', 'Canary')
    registry = runner.load_registry()
    agy = registry['agents']['agy']
    agy['permission_modes']['read_only'] = '--fixture-read-only'
    agy['invocation']['command'] = sys.executable
    agy['invocation']['args_template'].insert(0, str(script))
    run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    expected_argv = (sys.executable, str(script), *PLAN_INVOCATION)

    def verify(plan_run, spec):
        return (spec.argv == expected_argv and spec.cwd == root and
                plan_run.environment == run.environment and spec.env.get('REPO_ROOT') == str(root))

    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda registry: verify)
    return replace(config, agent_run=run.replace(agent='agy-cli', role='plan', model=GEMINI_4))


def plan_git_snapshot(root):
    from ai_skills.git import git_output

    queries = [('rev-parse', 'HEAD'), ('symbolic-ref', 'HEAD'), ('diff', '--binary'),
               ('diff', '--cached', '--binary'), ('ls-files', '--others', '--exclude-standard')]
    return [(root / 'canary').read_bytes(), *(git_output(str(root), *args) for args in queries)]


def test_protected_agy_plan_publishes_both_artifacts(tmp_path, monkeypatch):
    from ai_skills import runner
    from ai_skills.outcomes import outcome_path

    def unexpected_probe(*args, **kwargs):
        pytest.fail('a protected launch must not run a separate provider capability probe')

    monkeypatch.setattr(runner, '_check_cli_capabilities', unexpected_probe)
    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    run = config.agent_run
    before = plan_git_snapshot(run.repo_root)
    result = dispatch_worker(config)
    assert result.state == 'completed'
    assert json.loads(run.result_file.read_bytes()) == PLAN_RESULT
    assert (run.issue_dir / 'plan.md').read_text(encoding='utf-8') == PLAN_MARKDOWN
    assert plan_git_snapshot(run.repo_root) == before
    assert json.loads(outcome_path(config.state_file).read_bytes())['status'] == 'success'
    assert 'status=success|source=dispatcher' in run.done_file.read_text(encoding='utf-8')
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


@pytest.mark.parametrize(('mutation', 'wire', 'reason'), [
    ("envelope['plan_markdown'] = '  '", AGY_CAPTURE, 'missing-plan-file-artifact'),
    ("envelope['result']['complexity_level'] = 'unknown'", AGY_CAPTURE, 'invalid-plan-result-artifact'),
    ("envelope['result']['complexity_level'] = []", AGY_CAPTURE, 'invalid-plan-result-artifact'),
    ("envelope['result']['issue'] = '8'", AGY_CAPTURE, 'invalid-plan-result-artifact'),
    ("envelope['result']['commit_sha'] = 'unwanted'", AGY_CAPTURE, 'invalid-plan-result-artifact'),
    ("envelope['attempt_id'] = 'previous'", AGY_CAPTURE, 'capture-identity-mismatch'),
    ("del envelope['plan_markdown']", AGY_CAPTURE, 'capture-identity-mismatch'),
    ('', AGY_STEPS_ONLY, 'capture-response-invalid'),
    ('', AGY_CAPTURE + 'raise SystemExit(9)\n', 'provider-exit-nonzero'),
])
def test_protected_agy_plan_finalization_rejects_incomplete_capture(tmp_path, monkeypatch, mutation, wire, reason):
    config = protected_agy_plan_worker(tmp_path, monkeypatch, mutation=mutation, wire=wire)
    result = dispatch_worker(config)
    assert result.state == 'failed'
    assert result.reason == reason
    assert not config.agent_run.result_file.exists()
    assert not (config.agent_run.issue_dir / 'plan.md').exists()


@pytest.mark.parametrize('kind', ['occupied', 'symlink', 'hard-link', 'pair-alias', 'source', 'git', 'source-state', 'state-lock'])
def test_protected_agy_plan_rejects_unsafe_paths_before_launch(tmp_path, monkeypatch, kind):
    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    run = config.agent_run
    run.issue_dir.mkdir()
    plan = run.issue_dir / 'plan.md'
    if kind == 'occupied':
        plan.write_text('previous plan', encoding='utf-8')
    elif kind == 'symlink':
        plan.symlink_to(tmp_path / 'missing')
    elif kind == 'hard-link':
        os.link(run.repo_root / 'canary', plan)
    elif kind == 'pair-alias':
        config = replace(config, agent_run=run.replace(result_file=plan))
    elif kind == 'source':
        config = replace(config, agent_run=run.replace(result_file=run.repo_root / 'plan.json'))
    elif kind == 'git':
        config = replace(config, agent_run=run.replace(issue_dir=run.repo_root / '.git' / 'objects'))
    elif kind == 'source-state':
        config = replace(config, state_file=run.repo_root / 'unexpected' / 'worker.state.json')
    else:
        Path(str(config.state_file) + '.lock').symlink_to(run.repo_root, target_is_directory=True)

    with pytest.raises((ValueError, RuntimeError), match='occupied|unsafe|collision|outside'):
        dispatch_worker(config)
    assert not (tmp_path / 'calls').exists()
    assert not config.state_file.exists()
    assert not (run.repo_root / 'unexpected').exists()


@pytest.mark.parametrize('boundary', ['result', 'outcome', 'state', 'done'])
def test_protected_agy_plan_adopts_partial_publication(tmp_path, monkeypatch, boundary):
    from ai_skills import dispatch

    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    run = config.agent_run
    interrupt_foreground_dispatch(monkeypatch, config, boundary)
    assert_publication_boundary(config, boundary, 'success')
    plan = run.issue_dir / 'plan.md'
    assert plan.read_bytes() == PLAN_MARKDOWN.encode()
    plan_identity = plan.stat().st_ino
    mark_launcher_dead(config)
    assert dispatch.adopt_worker(config.state_file).state == 'completed'
    assert dispatch.adopt_worker(config.state_file).state == 'completed'
    assert json.loads(run.result_file.read_bytes()) == PLAN_RESULT
    assert plan.stat().st_ino == plan_identity
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'


def test_protected_agy_plan_adoption_rejects_changed_journaled_companion(tmp_path, monkeypatch):
    from ai_skills import dispatch

    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    run = config.agent_run
    interrupt_foreground_dispatch(monkeypatch, config, 'done')
    plan = run.issue_dir / 'plan.md'
    plan.write_text('foreign Markdown from another attempt', encoding='utf-8')
    mark_launcher_dead(config)
    result = dispatch.adopt_worker(config.state_file)
    assert (result.state, result.reason) == ('unknown', 'capture-plan-handoff-changed')
    assert not run.done_file.exists()
    admitted = dispatch._admitted_worker(config.state_file)
    assert not inspect_worker(admitted).artifact_valid


@pytest.mark.parametrize('kind', ['stray', 'conflict', 'stop', 'typed'])
def test_protected_agy_plan_trusts_only_the_bound_capture(tmp_path, monkeypatch, kind):
    from ai_skills import dispatch

    typed = """
del envelope['plan_markdown']
result = {key: envelope[key] for key in ('schema_version', 'run_id', 'attempt_id', 'issue', 'role')}
envelope['result'] = {**result, 'artifact_kind': 'role_failure', 'status': 'blocked',
                      'reason': 'plan-context-unavailable', 'summary': 'Missing input evidence'}
""" if kind == 'typed' else ''
    config = protected_agy_plan_worker(tmp_path, monkeypatch, mutation=typed,
        wire=AGY_STEPS_ONLY if kind == 'stray' else AGY_CAPTURE)
    run = config.agent_run
    plan = run.issue_dir / 'plan.md'
    publish = dispatch._publish_captured_result

    def before_capture(admitted, *, eligible):
        if kind in ('stray', 'conflict'):
            plan.parent.mkdir(exist_ok=True)
            plan.write_text('stray', encoding='utf-8')
        if kind == 'stray':
            run.result_file.write_text(json.dumps(PLAN_RESULT), encoding='utf-8')
        return publish(admitted, eligible=eligible)

    if kind == 'stop':
        interrupt_foreground_dispatch(monkeypatch, config, 'result')
        admitted = dispatch._admitted_worker(config.state_file)
        dispatch._record_stop_intent(admitted, 'interrupted')
        mark_launcher_dead(config)
        result = dispatch.adopt_worker(config.state_file)
        assert (result.state, result.reason, result.return_code) == ('interrupted', 'interrupted', 130)
        assert not run.result_file.exists()
        return

    monkeypatch.setattr(dispatch, '_publish_captured_result', before_capture)
    result = dispatch_worker(config)
    assert result.state == 'failed'
    assert not inspect_worker(dispatch._admitted_worker(config.state_file)).artifact_valid
    if kind == 'typed':
        assert result.reason == 'plan-context-unavailable'
        assert json.loads(run.result_file.read_bytes())['status'] == 'blocked'
        assert not plan.exists()
    else:
        reason = 'capture-response-invalid' if kind == 'stray' else 'capture-plan-artifact-conflict'
        assert result.reason == reason
        assert run.result_file.exists() == (kind == 'stray')
        assert plan.read_text(encoding='utf-8') == 'stray'


@pytest.mark.parametrize('change', ['agent', 'argv', 'permission', 'env', 'capture', 'profile', 'prompt'])
def test_protected_agy_plan_refuses_changed_runner_binding(tmp_path, monkeypatch, change):
    from ai_skills import dispatch, runner

    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    actual_run_agent = runner.run_agent

    def changed_run(run):
        changes = {'agent': {'agent': 'codex'}, 'argv': {'extra_args': ('--override',)},
            'permission': {'permission_mode': '--dangerously-skip-permissions'},
            'env': {'environment': {**run.environment, 'EXTRA_PROVIDER_SETTING': 'unverified'}},
            'capture': {'result_capture_context': None}, 'profile': {}, 'prompt': {}}
        if change == 'profile':
            monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda registry: None)
        if change == 'prompt':
            run.prompt_file.write_text('changed after admission', encoding='utf-8')
        with pytest.raises(ValueError, match='agy-plan-read-only-unavailable'):
            actual_run_agent(run.replace(**changes[change]))
        return runner.AgentRunResult(1, 'agy', GEMINI_4)

    monkeypatch.setattr(dispatch, 'run_agent', changed_run)
    assert dispatch_worker(config).state == 'failed'
    assert not (tmp_path / 'calls').exists()


def test_managed_non_agy_plan_still_accepts_direct_artifacts(tmp_path):
    config = worker_config(tmp_path, f"""
import json, os
from pathlib import Path

Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps({PLAN_RESULT!r}), encoding='utf-8')
Path(os.environ['RUN_WITH_IT_ISSUE_DIR'], 'plan.md').write_text({PLAN_MARKDOWN!r}, encoding='utf-8')
""")
    run = config.agent_run.replace(role='plan', issue_dir=tmp_path, cycle=1)
    config = replace(config, agent_run=run)
    assert dispatch_worker(config).state == 'completed'
    assert inspect_worker(config).artifact_valid


def test_agy_adoption_classifies_only_structured_diagnostics_and_records_canonical_route(tmp_path, monkeypatch):
    import socket
    from ai_skills import dispatch, routing
    from ai_skills.outcomes import outcome_path, pending_outcome
    from ai_skills.processes import Liveness, ProcessIdentity, ProcessSupervisor
    from ai_skills.storage import load_json, write_json_atomic

    def prepare_agy_adoption(case_dir, *, agent_name, stdout_bytes, stderr_bytes, exit_code):
        case_dir.mkdir(parents=True, exist_ok=True)
        config = agy_gemini_worker(case_dir, monkeypatch, AGY_CAPTURE)
        registry = json.loads(config.agent_run.registry_file.read_text(encoding='utf-8'))
        registry.setdefault('aliases', {})['antigravity'] = 'agy'
        config.agent_run.registry_file.write_text(json.dumps(registry), encoding='utf-8')
        stream_dir = case_dir / 'streams'
        stream_dir.mkdir()
        stdout_file = stream_dir / 'stdout'
        stderr_file = stream_dir / 'stderr'
        stdout_file.write_bytes(stdout_bytes)
        stderr_file.write_bytes(stderr_bytes)
        run_id, attempt_id = 'agy-adopt-run', 'agy-adopt-attempt'
        rec_dir = case_dir / 'processes'
        run = config.agent_run.replace(
            agent=agent_name, model=GEMINI_4, dispatcher_managed=True,
            state_file=config.state_file, stream_directory=stream_dir,
            process_state_file=Path(str(config.state_file) + '.process.json'),
            record_directory=rec_dir, run_id=run_id, attempt_id=attempt_id,
        )
        config = replace(config, agent_run=run)
        write_json_atomic(Path(str(config.state_file) + '.config.json'), config.to_record())
        write_json_atomic(outcome_path(config.state_file), pending_outcome(
            run_id=run_id, attempt_id=attempt_id, issue=str(run.issue), role=run.role,
            state_file=config.state_file, expected_artifacts=dispatch.expected_artifact_paths(config)))
        identity = ProcessIdentity(pid=99999, created_at=1234.0, host=socket.gethostname(),
                                   run_id=run_id, attempt_id=attempt_id)
        write_json_atomic(run.process_state_file, {
            'schema_version': 1, 'identity': identity.__dict__,
            'record_directory': str(rec_dir), 'state': 'alive',
            'stdout_file': str(stdout_file), 'stderr_file': str(stderr_file),
        })
        write_json_atomic(config.state_file, {
            'schema_version': 1, 'state': 'running', 'run_id': run_id, 'attempt_id': attempt_id,
            'provider_identity': identity.__dict__,
            'launcher_identity': {'pid': 99998, 'created_at': 1.0, 'host': socket.gethostname()},
        })
        monkeypatch.setattr(ProcessSupervisor, 'inspect', lambda self, ident: Liveness('dead', 'exited'))
        monkeypatch.setattr(ProcessSupervisor, 'return_code', lambda self, ident: exit_code)
        return config

    # 1. Model-controlled stdout containing "quota" / "rate limit" must NOT classify as infrastructure.
    spoof_stdout = (json.dumps({'event': 'step_update', 'step_update': {'text': 'quota exceeded rate limit'}}) + '\n').encode()
    spoof_cfg = prepare_agy_adoption(tmp_path / 'spoof', agent_name='agy',
                                     stdout_bytes=spoof_stdout, stderr_bytes=b'', exit_code=1)
    spoof_res = dispatch.adopt_worker(spoof_cfg.state_file)
    assert spoof_res.state == 'failed'
    spoof_state = load_json(spoof_cfg.state_file)
    assert spoof_state['failure_class'] == 'capability'
    assert routing.failed_attempt_routes([spoof_cfg.state_file]) == []

    # 2. Structured AGY terminal ProviderError + aliased agent classifies via agy_diagnostics and records canonical route.
    err_stdout = (
        json.dumps({'event': 'init', 'conversation_id': 'c1', 'init': {'model': GEMINI_4}}) + '\n' +
        json.dumps({'event': 'step_update', 'step_update': {'conversation_id': 'c1', 'step_index': 0,
                                                            'step_type': 'user_input', 'state': 'DONE'}}) + '\n' +
        json.dumps({'event': 'result', 'result': {'conversation_id': 'c1', 'status': 'ERROR',
                                                  'error': f'Not entitled to {GEMINI_4}'}}) + '\n'
    ).encode()
    err_cfg = prepare_agy_adoption(tmp_path / 'entitlement', agent_name='antigravity',
                                   stdout_bytes=err_stdout, stderr_bytes=b'', exit_code=0)
    err_res = dispatch.adopt_worker(err_cfg.state_file)
    assert err_res.state == 'failed'
    assert err_res.reason == 'model-unsupported'
    err_state = load_json(err_cfg.state_file)
    assert (err_state['failure_class'], err_state['agent'], err_state['model']) == ('infrastructure', 'agy', GEMINI_4)
    avail = routing.availability_from_failed_attempts([err_cfg.state_file])
    assert avail['pairs'] == {('agy', GEMINI_4)} and not avail['agents']


def test_publish_protected_plan_rolls_back_plan_markdown_on_result_conflict(tmp_path, monkeypatch):
    from ai_skills import dispatch

    config = protected_agy_plan_worker(tmp_path, monkeypatch)
    plan, result = dispatch._plan_paths(config.agent_run)
    original_write = dispatch.write_text_atomic

    def fail_on_result(path, text, *, exclusive=False):
        if Path(path) == result:
            raise FileExistsError(str(path))
        return original_write(path, text, exclusive=exclusive)

    monkeypatch.setattr(dispatch, 'write_text_atomic', fail_on_result)
    reason, proof = dispatch._publish_protected_plan(config, PLAN_RESULT, PLAN_MARKDOWN)
    assert (reason, proof) == ('capture-plan-artifact-conflict', None)
    assert not plan.exists()
    assert not result.exists()


def test_dispatch_waits_briefly_for_delayed_provider_return_code(tmp_path, monkeypatch):
    from ai_skills.processes import ProcessSupervisor

    config = worker_config(
        tmp_path,
        "import json, os; from pathlib import Path; "
        "Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps({'ok': True}), encoding='utf-8')",
    )
    original = ProcessSupervisor.return_code
    calls = [0]

    def delayed_return_code(self, identity):
        calls[0] += 1
        if calls[0] < 3:
            return None
        return original(self, identity)

    monkeypatch.setattr(ProcessSupervisor, 'return_code', delayed_return_code)
    result = dispatch_worker(config)
    assert result.state == 'completed'
    assert result.return_code == 0
    assert calls[0] >= 3
