import json
import os
from pathlib import Path
import sys
import time

from ai_skills.dispatch import WorkerConfig, dispatch_worker, inspect_worker
from ai_skills.runner import AgentRunConfig


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


def test_adopt_dead_dispatcher_never_relaunches_paid_attempt(tmp_path):
    import psutil
    from ai_skills.dispatch import adopt_worker
    config=worker_config(tmp_path, '''import os,time
from pathlib import Path
with Path('launch-count').open('a') as stream: stream.write('one\\n')
time.sleep(.3)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''',detach=True)
    result=dispatch_worker(config)
    wrapper=json.loads(Path(str(config.state_file)+'.dispatcher.process.json').read_text())
    process=psutil.Process(wrapper['identity']['pid'])
    assert process.create_time()==wrapper['identity']['created_at']
    process.kill()
    assert adopt_worker(config.state_file).state in ('running','completed')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        result=adopt_worker(config.state_file)
        if result.state!='running': break
        time.sleep(.03)
    assert result.state=='completed', result.reason
    assert (tmp_path/'launch-count').read_text().splitlines()==['one']


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
    assert inspect_worker(config).log_tail_changed is True
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
