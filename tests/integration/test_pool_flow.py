import json
from ai_skills.pool import PoolConfig, run_pool

def test_validate_only_never_launches(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    state=tmp_path/'state.json';state.write_text(json.dumps({'issue_registry':{},'execution_plan':{'topo_order':[]}}))
    monkeypatch.setattr(pool,'dispatch_worker',lambda config: (_ for _ in ()).throw(AssertionError('launch')))
    result=run_pool(PoolConfig(state_file=state,validate_only=True))
    assert result.state == 'validated'

from pathlib import Path
import pytest
from pool_support import pool_fixture,FakePoolDriver,install_fake
from ai_skills.pool import PoolSupervisor

def test_slots_refill_and_dependencies_only_admit_after_completion(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{},2:{},3:{'deps':[1]},4:{}},parallel=2)
    driver=FakePoolDriver(delays={'1':2,'2':0,'3':0,'4':0});install_fake(monkeypatch,driver)
    result=run_pool(config)
    assert result.state=='pool-empty'
    assert [str(item.agent_run.issue) for item in driver.started]==['1','2','4','3']
    assert driver.maximum<=2
    state=json.loads(Path(config.state_file).read_text())
    assert all(entry['status']=='completed' for entry in state['issue_registry'].values())
    events=config.resolved().events_log.read_text()
    assert 'type=run-board' in events and 'type=pool-empty' in events and 'type=pool-heartbeat' in events

def test_scope_overlap_and_exclusive_issue_never_coschedule(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{'parallel_safe':True,'ownership_scope':['src/a']},2:{'parallel_safe':True,'ownership_scope':['src/a/child']},3:{'parallel_safe':False,'ownership_scope':['src/c']}},parallel=3,strict=True)
    driver=FakePoolDriver(delays={'1':2,'2':1});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert driver.maximum==1

def test_missing_context_keeps_pool_available_for_late_context(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config=pool_fixture(tmp_path,{1:{}})
    context=tmp_path/'context-1.md';context.unlink()
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    real_sleep=pool.time.sleep
    def late(seconds):
        context.write_text('late context')
        real_sleep(.001)
    monkeypatch.setattr(pool.time,'sleep',late)
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==1
    assert 'type=pool-waiting-context|count=1' in config.resolved().events_log.read_text()

def test_subcoordinator_failure_resumes_once_without_losing_state(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{}})
    driver=FakePoolDriver(outcomes={'1':['crash','completed']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==2
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['1']['sub_coord_recovery_attempts']==1
    assert state['issue_registry']['1']['status']=='completed'
    assert (Path(driver.started[1].agent_run.issue_dir)/'sub-state.json').is_file()
    assert 'SUB_COORD_RECOVERY_MODE=1' in Path(driver.started[1].agent_run.context_file).read_text()

def test_recovery_budget_exhaustion_blocks_without_spawning_more(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{}})
    driver=FakePoolDriver(outcomes={'1':['crash']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==3
    entry=json.loads(Path(config.state_file).read_text())['issue_registry']['1']
    assert entry['status']=='blocked'
    assert 'sub-coordinator-recovery-attempts-exhausted' in entry['blocking_reasons']

def test_compaction_has_separate_larger_budget(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{'sub_coord_recovery_attempts':2}})
    driver=FakePoolDriver(outcomes={'1':['compaction','completed']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    entry=json.loads(Path(config.state_file).read_text())['issue_registry']['1']
    assert entry['sub_coord_recovery_attempts']==2 and entry['sub_coord_compaction_handoffs']==1
    assert len(driver.started)==2

def test_merge_recovery_uses_same_owned_pool_slot(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{},2:{'deps':[1]}},parallel=1)
    driver=FakePoolDriver(outcomes={'1':['merge_failed','completed']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert [item.agent_run.role for item in driver.started]==['sub-coord','merge-recovery','sub-coord']
    assert driver.maximum==1
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['1']['status']=='completed'
    assert state['issue_registry']['2']['status']=='completed'

def test_dry_run_does_not_create_worker_launch_intent(tmp_path,monkeypatch,capsys):
    from dataclasses import replace
    config=replace(pool_fixture(tmp_path,{1:{}}),dry_run=True)
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    assert run_pool(config).state=='dry-run'
    assert not driver.started
    assert not json.loads(Path(config.state_file).read_text())['pool_attempts']
    assert 'worker_config' not in capsys.readouterr().out

@pytest.mark.parametrize('mode',['completed','crash','compaction','merge_failed'])
def test_real_dispatcher_pool_flows_with_only_python_fixture_provider(tmp_path,monkeypatch,mode):
    from pool_support import real_pool_fixture
    monkeypatch.setenv('RUN_WITH_IT_GITHUB_UPDATES','0')
    config=real_pool_fixture(tmp_path,mode=mode)
    assert run_pool(config).state=='pool-empty'
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['1']['status']=='completed'
    launches=[json.loads(line) for line in (tmp_path/'launches.log').read_text().splitlines()]
    assert len(launches)==(1 if mode=='completed' else 2)
    assert all(item['role'] in ('sub-coord','merge-recovery') for item in launches)

def test_bootstrap_failures_are_bounded_separately_from_recovery(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    from ai_skills.dispatch import WorkerResult
    from ai_skills.storage import write_json_atomic
    config=pool_fixture(tmp_path,{1:{}})
    started=[]
    def failed(worker):
        started.append(worker)
        Path(worker.state_file).parent.mkdir(parents=True,exist_ok=True)
        write_json_atomic(worker.state_file,{'state':'failed','reason':'dispatch-bootstrap-failed','return_code':1})
        return WorkerResult('failed',1,Path(worker.state_file),'dispatch-bootstrap-failed')
    monkeypatch.setattr(pool,'dispatch_worker',failed)
    monkeypatch.setattr(pool,'adopt_worker',lambda path:WorkerResult('failed',1,Path(path),'dispatch-bootstrap-failed'))
    monkeypatch.setattr(pool,'update_github',lambda request:None)
    assert run_pool(config).state=='pool-empty'
    assert len(started)==3
    entry=json.loads(Path(config.state_file).read_text())['issue_registry']['1']
    assert entry['spawn_bootstrap_attempts']==3
    assert not entry.get('sub_coord_recovery_attempts')

def test_unreachable_dependency_cone_is_reported(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{},2:{'deps':[1]},3:{'deps':[2]}})
    driver=FakePoolDriver(outcomes={'1':['failed-review']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==1
    events=config.resolved().events_log.read_text()
    assert 'type=pool-unreachable' in events
    assert '2:1;3:1' in events

def test_merge_recovery_preserves_original_issue_report(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{}},parallel=1)
    driver=FakePoolDriver(outcomes={'1':['merge_failed','completed']});install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    original=Path(driver.started[0].agent_run.result_file)
    assert json.loads(original.read_text())['outcome']=='merge_failed'
    assert json.loads(Path(driver.started[1].agent_run.result_file).read_text())['outcome']=='completed'

def test_heartbeat_contains_pid_all_status_counts_and_repeats_without_board_change(tmp_path,monkeypatch):
    import os
    import ai_skills.pool as pool
    config=pool_fixture(tmp_path,{1:{'status':'completed'},2:{'status':'in_progress'},3:{'deps':[1]},4:{'status':'blocked'},5:{'status':'failed-review'},6:{'status':'failed-merge'},7:{'status':'merge_recovery'},8:{'deps':[4]}})
    saved=json.loads(Path(config.state_file).read_text())
    saved['issue_registry']['3']['context_file']=str(tmp_path/'missing')
    saved['issue_registry']['8']['context_file']=str(tmp_path/'also-missing')
    Path(config.state_file).write_text(json.dumps(saved))
    supervisor=PoolSupervisor(config);supervisor.active={'2':object()}
    now=[100.0];monkeypatch.setattr(pool.time,'monotonic',lambda:now[0])
    supervisor.board();supervisor.board()
    now[0]+=config.heartbeat_seconds
    supervisor.board()
    events=config.resolved().events_log.read_text().splitlines()
    heartbeats=[line for line in events if 'type=pool-heartbeat|' in line]
    assert len(heartbeats)==2
    assert len([line for line in events if 'type=run-board|' in line])==1
    fields=dict(part.split('=',1) for part in heartbeats[0].split('|')[1:])
    assert fields=={'type':'pool-heartbeat','pool_pid':str(os.getpid()),'active':'1','parallel_jobs':'2','total':'8','completed':'1','in_progress':'1','pending':'2','blocked':'1','failed_review':'1','failed_merge':'1','merge_recovery':'1','waiting_context':'1'}


def test_admission_deferrals_emit_only_on_change_outside_state_lock(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{'status':'in_progress','parallel_safe':True,'ownership_scope':['src/a']},2:{'parallel_safe':True,'ownership_scope':['src/a/child']},3:{'parallel_safe':True,'ownership_scope':['src/c']}},parallel=3,strict=True)
    saved=json.loads(Path(config.state_file).read_text());saved['active_pool_issues']=['1']
    Path(config.state_file).write_text(json.dumps(saved))
    supervisor=PoolSupervisor(config)
    original=supervisor.emit
    def emit(kind,**fields):
        assert not Path(str(config.state_file)+'.lock').exists()
        return original(kind,**fields)
    monkeypatch.setattr(supervisor,'emit',emit)
    assert supervisor.ready(3).issues==('3',)
    supervisor.ready(3)
    events=config.resolved().events_log.read_text().splitlines()
    deferrals=[line for line in events if 'type=pool-admission-deferred|' in line]
    assert len(deferrals)==1
    assert '|count=1|deferrals=2:concurrency-conflict-with-1' in deferrals[0]


@pytest.mark.parametrize('outcome', ['blocked','failed-review','failed-merge'])
def test_real_terminal_negative_report_handoff_never_satisfies_dependency_or_relaunches(tmp_path, monkeypatch, outcome):
    from pool_support import real_pool_fixture
    config=real_pool_fixture(tmp_path,mode=outcome)
    state=json.loads(Path(config.state_file).read_text())
    state['issue_registry']['2']={'status':'pending','deps':[1],'context_file':str(tmp_path/'context-1.md')}
    state['execution_plan']['topo_order']=[1,2]
    Path(config.state_file).write_text(json.dumps(state))
    assert run_pool(config).state=='pool-empty'
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['1']['status']==outcome
    assert state['issue_registry']['2']['status']=='pending'
    launches=[json.loads(line) for line in (tmp_path/'launches.log').read_text().splitlines()]
    assert len(launches)==1
    worker=state['pool_attempts']['1']['worker_config']
    journal=json.loads(Path(worker['state_file']+'.outcome.json').read_text())
    assert journal['status']=='success' and journal['issue_outcome']==outcome
    assert journal['worker_return_code']==0


@pytest.mark.parametrize('mode', ['malformed-terminal','explicit-failure'])
def test_failed_handoff_with_raw_completed_report_recovers_without_false_issue_completion(tmp_path, monkeypatch, mode):
    from pool_support import real_pool_fixture
    config=real_pool_fixture(tmp_path,mode=mode)
    script=tmp_path/'provider.py'
    source=script.read_text()
    old="result.write_text(json.dumps({'outcome':'completed' if recovery else mode,'summary':'real fake provider','verification':{'passed':True,'evidence':'fixture'},'blocking_reasons':['fixture blocker'] if mode=='blocked' and not recovery else []}))"
    new="""report={'outcome':'completed','summary':'real fake provider','verification':{'passed':True,'evidence':'fixture'}}
if not recovery:
    (Path(os.environ['RUN_WITH_IT_ISSUE_DIR'])/'sub-state.json').write_text(json.dumps({'phase':'impl','in_flight_agents':[]}))
    if mode=='malformed-terminal': report={'outcome':'completed'}
    else: report.update(schema_version=1,artifact_kind='role_failure',run_id=os.environ['RUN_WITH_IT_RUN_ID'],attempt_id=os.environ['RUN_WITH_IT_ATTEMPT_ID'],issue=issue,role=role,status='blocked',reason='verification-unavailable',verification={'passed':False})
result.write_text(json.dumps(report))"""
    assert old in source
    script.write_text(source.replace(old,new))
    assert run_pool(config).state=='pool-empty'
    launches=(tmp_path/'launches.log').read_text().splitlines()
    assert len(launches)==2
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['1']['status']=='completed'
    assert state['issue_registry']['1']['sub_coord_recovery_attempts']==1
    reports=list((tmp_path/'.run-with-it/issues/1/recovery').rglob('report.json'))
    assert reports and any(json.loads(path.read_text()).get('outcome')=='completed' for path in reports)


@pytest.mark.parametrize('damage', ['missing','foreign-attempt','wrong-path'])
def test_pool_never_consumes_unbound_success_sidecar(tmp_path, monkeypatch, damage):
    from ai_skills.outcomes import outcome_path
    from ai_skills.storage import write_json_atomic
    config=pool_fixture(tmp_path,{1:{},2:{'deps':[1]}})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    original=driver.adopt
    def corrupted(path):
        result=original(path)
        journal_path=outcome_path(path)
        if result.state=='completed':
            journal=json.loads(journal_path.read_text())
            if damage=='missing':journal_path.unlink()
            elif damage=='foreign-attempt':
                journal['attempt_id']='foreign';write_json_atomic(journal_path,journal)
            else:
                journal['expected_artifacts']['result']=str(tmp_path/'foreign.json');write_json_atomic(journal_path,journal)
        return result
    monkeypatch.setattr('ai_skills.pool.adopt_worker',corrupted)
    assert run_pool(config).state=='ownership-unknown'
    assert len(driver.started)==1
    state=json.loads(Path(config.state_file).read_text())
    assert state['issue_registry']['2']['status']=='pending'


@pytest.mark.parametrize('role', ['sub-coord','merge-recovery'])
def test_failed_dispatch_cannot_finalize_raw_completed_report(tmp_path, monkeypatch, role):
    from ai_skills.dispatch import WorkerResult
    config=pool_fixture(tmp_path,{1:{}})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    supervisor=PoolSupervisor(config);supervisor.initialize()
    worker=supervisor.worker('1',kind=role)
    supervisor.launch('1',worker)
    driver.adopt(worker.state_file)
    monkeypatch.setattr('ai_skills.pool.adopt_worker',lambda path:WorkerResult('failed',1,Path(path),'provider-exit-nonzero'))
    supervisor.tick()
    state=supervisor.read()
    assert state['issue_registry']['1']['status']=='blocked'
    assert json.loads(Path(worker.agent_run.result_file).read_text())['outcome']=='completed'
    policy=Path(state['issue_registry']['1']['report_file'])
    assert policy != Path(worker.agent_run.result_file)
    assert json.loads(policy.read_text())['outcome']=='blocked'


def test_policy_report_storage_failure_never_overwrites_provider_bytes(tmp_path, monkeypatch):
    from ai_skills.dispatch import WorkerResult
    config=pool_fixture(tmp_path,{1:{}})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    supervisor=PoolSupervisor(config);supervisor.initialize()
    worker=supervisor.worker('1',kind='merge-recovery')
    supervisor.launch('1',worker);driver.adopt(worker.state_file)
    original=Path(worker.agent_run.result_file).read_bytes()
    monkeypatch.setattr('ai_skills.pool.adopt_worker',lambda path:WorkerResult('failed',1,Path(path),'provider-exit-nonzero'))
    monkeypatch.setattr(supervisor,'write_policy_report',lambda *args:(_ for _ in ()).throw(OSError('policy report storage failure')))
    with pytest.raises(OSError,match='policy report storage'):
        supervisor.tick()
    assert Path(worker.agent_run.result_file).read_bytes()==original
    assert len(driver.started)==1
