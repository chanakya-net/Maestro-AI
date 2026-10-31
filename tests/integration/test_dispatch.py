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
