import json
import os
from pathlib import Path
import sys
import time

import pytest

from ai_skills.dispatch import WorkerConfig, dispatch_worker, inspect_worker
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
    plan={'schema_version':1,'issue':'7','role':'plan','status':'success','approach':'Build','complexity_level':'easy','slices':[{'id':1}]}
    config.agent_run.result_file.write_text(json.dumps(plan))
    (tmp_path/'plan.md').write_text('')
    snapshot=inspect_worker(replace(config,agent_run=config.agent_run.replace(role='plan',issue_dir=tmp_path)))
    assert snapshot.artifact_reason=='missing-plan-file-artifact'


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
                {**explicit, 'review_comment_ids': ['id']}):
        with pytest.raises(ValueError):
            WorkerConfig.from_sources({**inputs, 'result_capture': bad}, {})
    assert not config.state_file.exists()


CAPTURE_CHECKS = {'required_checks': ['feature check', 'git check'], 'review_comment_ids': []}


def capture_worker(tmp_path, body, *, help_flag=True, **options):
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
    script.write_text(f'''import json, os, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ['--help']:
    print({help_text!r})
    raise SystemExit(0)
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


@pytest.mark.parametrize('variant', ['supported', 'unsupported', 'legacy', 'claude', 'modify', 'review', 'plan', 'no-cycle'])
def test_capture_binding_recorded_only_for_eligible_codex_attempts(tmp_path, variant):
    bound = variant == 'supported'
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
''', help_flag=variant != 'unsupported', result_capture=None if variant == 'legacy' else CAPTURE_CHECKS)
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


def test_adoption_replays_captured_handoff_without_relaunch(tmp_path, monkeypatch):
    from ai_skills import dispatch

    config = capture_worker(tmp_path, BUILD_CAPTURE + '''
capture.write_text(json.dumps(envelope), encoding='utf-8')
''', result_capture=CAPTURE_CHECKS)
    original = dispatch.write_text_atomic

    def failed_storage(path, value, *, exclusive=False):
        if exclusive:
            raise OSError('injected captured result publication failure')
        return original(path, value)

    monkeypatch.setattr(dispatch, 'write_text_atomic', failed_storage)
    with pytest.raises(OSError, match='injected captured result publication failure'):
        dispatch_worker(config)
    assert not config.agent_run.result_file.exists()
    journal = Path(str(config.state_file) + '.outcome.json')
    assert json.loads(journal.read_bytes())['status'] == 'pending'
    capture = Path(str(config.state_file) + '.streams') / 'final-response.json'
    original_capture = capture.read_bytes()
    state = json.loads(config.state_file.read_bytes())
    state['launcher_identity']['created_at'] += 1
    dispatch.write_json_atomic(config.state_file, state)
    monkeypatch.setattr(dispatch, 'write_text_atomic', original)
    assert dispatch.adopt_worker(config.state_file).state == 'completed'
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'
    assert capture.read_bytes() == original_capture
    assert json.loads(config.agent_run.result_file.read_bytes()) == json.loads(original_capture)['result']


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
