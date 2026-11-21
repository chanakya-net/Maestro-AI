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

def test_live_child_keeps_slot_past_hour_until_terminal_artifact(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    from ai_skills.storage import write_json_atomic
    config=pool_fixture(tmp_path,{1:{}},parallel=1)
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    supervisor=PoolSupervisor(config);assert supervisor.initialize() is None
    coordinator=supervisor.worker('1')
    issue_dir=Path(coordinator.agent_run.issue_dir);issue_dir.mkdir(parents=True,exist_ok=True)
    child_state=issue_dir/'child.state.json'
    child_result=issue_dir/'child-result.json'
    child_done=issue_dir/'child.done'
    child={'role':'impl','cycle':1,'state_file':str(child_state),
           'result_file':str(child_result),'done_file':str(child_done)}
    child_state.write_text(json.dumps({'state':'running','updated_at':'1970-01-01T02:46:39Z'}))
    (issue_dir/'sub-state.json').write_text(json.dumps({'phase':'impl','in_flight_agents':[child]}))
    supervisor._intent('1',coordinator)
    supervisor.store.transaction(lambda state:state['issue_registry']['1'].update(worker_wait_since=6399))
    supervisor.active['1']=coordinator
    monkeypatch.setattr(pool.time,'time',lambda:10000)
    monkeypatch.setattr(pool.state_policy,'path_mtime',lambda path:None)

    supervisor.recover('1',coordinator)
    assert supervisor.active['1'] is coordinator
    assert driver.started==[]

    child_state.write_text(json.dumps({'state':'completed','updated_at':'1970-01-01T02:46:39Z','done':True}))
    write_json_atomic(child_result,{'outcome':'completed','summary':'late child result'})
    child_done.write_text('completed')
    supervisor.recover('1',coordinator)
    assert len(driver.started)==1
    recovery=driver.started[0]
    assert recovery.agent_run.role=='sub-coord'
    assert 'SUB_COORD_RECOVERY_MODE=1' in Path(recovery.agent_run.context_file).read_text()

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


@pytest.mark.parametrize(('mode','roles'),[
    ('completed',['sub-coord']),
    ('crash',['sub-coord','sub-coord']),
    ('merge_failed',['sub-coord','merge-recovery']),
])
def test_agy_coordinator_selection_stays_independent_from_worker_overrides(tmp_path,monkeypatch,mode,roles):
    from dataclasses import replace
    from pathlib import Path

    config=pool_fixture(tmp_path,{1:{}})
    config=replace(config,sub_coord_agent='agy',sub_coord_model='gemini-4-neon-medium',
        forced_agent='claude',forced_model='claude-sonnet-5-5',
        registry_file=Path(__file__).parents[2]/'assets'/'agent-registry.json')
    driver=FakePoolDriver(outcomes={'1':[mode,'completed']});install_fake(monkeypatch,driver)

    assert run_pool(config).state=='pool-empty'
    assert [item.agent_run.role for item in driver.started]==roles
    for item in driver.started:
        assert (item.agent_run.agent,item.agent_run.model)==('agy','gemini-4-neon-medium')
        assert item.agent_run.environment['FORCED_AGENT']=='claude'
        assert item.agent_run.environment['FORCED_MODEL']=='claude-sonnet-5-5'
        assert item.agent_run.environment.get('AGENT') is None
        assert item.agent_run.environment.get('MODEL') is None


def test_invalid_agy_coordinator_pair_is_rejected_before_dispatch(tmp_path,monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    config=pool_fixture(tmp_path,{1:{}})
    config=replace(config,sub_coord_agent='agy',sub_coord_model='unknown-model',
        registry_file=Path(__file__).parents[2]/'assets'/'agent-registry.json')
    driver=FakePoolDriver();install_fake(monkeypatch,driver)

    with pytest.raises(ValueError,match='unsupported coordinator model.*unknown-model.*AGY'):
        run_pool(config)
    assert driver.started==[]


def _custom_agy_registry(tmp_path,models):
    registry=tmp_path/'custom-registry.json'
    registry.write_text(json.dumps({'agents':{'agy':{'model':{'known_models':list(models)}}}}))
    return registry


def test_agent_registry_override_validates_and_forwards_the_same_registry(tmp_path,monkeypatch):
    from dataclasses import replace
    from ai_skills.registry import load_registry
    from ai_skills.routing import load_run_registry
    registry=_custom_agy_registry(tmp_path,['custom-agy-model'])
    monkeypatch.setenv('AGENT_REGISTRY_FILE',str(registry))
    config=replace(pool_fixture(tmp_path,{1:{}}),sub_coord_agent='agy',sub_coord_model='custom-agy-model')
    driver=FakePoolDriver();install_fake(monkeypatch,driver)

    assert run_pool(config).state=='pool-empty'
    run=driver.started[0].agent_run
    assert (run.agent,run.model)==('agy','custom-agy-model')
    assert Path(run.registry_file)==registry
    assert run.environment['AGENT_REGISTRY_FILE']==str(registry)
    assert load_run_registry('',tmp_path/'main-state.json')==load_registry(registry)


def test_model_absent_from_agent_registry_override_is_rejected_before_inference(tmp_path,monkeypatch):
    from dataclasses import replace
    registry=_custom_agy_registry(tmp_path,['custom-agy-model'])
    monkeypatch.setenv('AGENT_REGISTRY_FILE',str(registry))
    # Valid in the packaged registry, but the operator's override does not list it.
    config=replace(pool_fixture(tmp_path,{1:{}}),sub_coord_agent='agy',sub_coord_model='gemini-4-neon-medium')
    driver=FakePoolDriver();install_fake(monkeypatch,driver)

    with pytest.raises(ValueError,match='unsupported coordinator model.*gemini-4-neon-medium.*AGY'):
        run_pool(config)
    assert driver.started==[]

def test_dry_run_does_not_create_worker_launch_intent(tmp_path,monkeypatch,capsys):
    from dataclasses import replace
    config=replace(pool_fixture(tmp_path,{1:{}}),dry_run=True)
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    assert run_pool(config).state=='dry-run'
    assert not driver.started
    assert not json.loads(Path(config.state_file).read_text())['pool_attempts']
    assert 'worker_config' not in capsys.readouterr().out


@pytest.mark.parametrize('kind', ['sub-coord', 'merge-recovery'])
def test_pool_coordinator_configs_have_no_default_deadline(tmp_path, kind):
    config = pool_fixture(tmp_path, {1:{}})
    supervisor = PoolSupervisor(config)
    assert supervisor.initialize() is None
    worker = supervisor.worker('1', kind=kind)
    assert worker.timeout_seconds == worker.hard_limit_seconds == 0
    assert (worker.quiet_seconds, worker.stall_seconds) == (900, 1200)

def test_pool_recovery_wait_defaults_unbounded_and_allows_positive_override(tmp_path):
    default=PoolConfig(state_file=tmp_path/'state.json')
    override=PoolConfig.from_sources({'max_worker_wait_seconds':3600},{})
    assert (default.sub_coord_agent,default.sub_coord_model)==('codex','gpt-6.2-sol')
    assert default.max_worker_wait_seconds==0
    assert override.max_worker_wait_seconds==3600
    assert default.worker_stale_seconds==1200


@pytest.mark.parametrize('mode', ['completed', 'crash', 'merge_failed'])
def test_native_pool_coordinator_attempts_use_unbounded_defaults(tmp_path, monkeypatch, mode):
    from dataclasses import replace
    from pool_support import real_pool_fixture

    monkeypatch.setenv('RUN_WITH_IT_GITHUB_UPDATES', '0')
    config = replace(real_pool_fixture(tmp_path, delay=.2, mode=mode), timeout_seconds=0, hard_limit_seconds=0)
    assert run_pool(config).state == 'pool-empty'
    attempts = json.loads(Path(config.state_file).read_text())['pool_attempts']['1']
    assert attempts['worker_config']['timeout_seconds'] == 0
    assert attempts['worker_config']['hard_limit_seconds'] == 0

@pytest.mark.parametrize(('mode','labels'),[
    ('completed',[('normal','sub-coord')]),
    ('crash',[('recovering','sub-coord')]),
    ('merge_failed',[('normal','sub-coord'),('merge-recovery','merge-recovery')]),
])
def test_default_pool_coordinator_attempts_outlive_both_former_limits(tmp_path,monkeypatch,mode,labels):
    from pool_support import install_simulated_dispatch,real_pool_fixture
    records=install_simulated_dispatch(monkeypatch)
    config=real_pool_fixture(tmp_path,delay=1,mode=mode,limits={})
    assert (config.timeout_seconds,config.hard_limit_seconds)==(0,0)
    assert run_pool(config).state=='pool-empty'
    completed=[item for item in records if item.result.state=='completed']
    assert [item.config.agent_run.role for item in completed]==[role for _,role in labels]
    for item,(label,_) in zip(completed,labels):
        assert item.config.timeout_seconds==item.config.hard_limit_seconds==0,label
        assert item.simulated_seconds>7200,label
        assert ('SUB_COORD_RECOVERY_MODE=1' in Path(item.config.agent_run.context_file).read_text())==(label=='recovering')

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


def test_recovery_wait_expiry_is_visible(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from ai_skills import pool, state
    from ai_skills.storage import write_json_atomic
    from pool_support import recovery_child

    driver = FakePoolDriver()
    install_fake(monkeypatch, driver)
    config = replace(pool_fixture(tmp_path, {1:{}, 2:{}}, parallel=1), max_worker_wait_seconds=60)
    supervisor = PoolSupervisor(config)
    assert supervisor.initialize() is None
    worker = supervisor.worker('1')
    supervisor._intent('1', worker)
    supervisor.active['1'] = worker
    issue_dir = worker.agent_run.issue_dir
    _, ledger, ownership = recovery_child(issue_dir, monkeypatch)
    write_json_atomic(issue_dir/'sub-state.json', {'phase':'complexity','in_flight_agents':[ledger]})
    clock = SimpleNamespace(now=1000)
    monkeypatch.setattr(pool, 'time', SimpleNamespace(time=lambda:clock.now, monotonic=lambda:1))

    for elapsed in (0, 59, 60, 61, 90):
        clock.now = 1000 + elapsed
        supervisor.recover('1', worker)
        supervisor.board()
        saved = supervisor.read()['issue_registry']['1']
        assert saved['worker_wait_since'] == 1000
        assert saved.get('sub_coord_recovery_attempts', 0) == 0
        assert supervisor.active == {'1':worker}
        stage = state.issue_stage(saved, set())
        if elapsed >= 60:
            assert stage == 'recovery-wait-expired(complexity cycle 1, live, 60s limit)'
            assert saved['worker_wait']['elapsed_seconds'] == 60
        else:
            assert 'recovery-wait-expired' not in stage
    assert not driver.started
    events = config.resolved().events_log.read_text().splitlines()
    expired, = [line for line in events if 'type=sub-coord-recovery-wait-expired' in line]
    for field in ('elapsed_seconds=60', 'max_wait_seconds=60', 'ownership=live', 'worker_attempt=child-attempt'):
        assert field in expired
    assert any('type=run-board' in line and 'recovery-wait-expired' in line for line in events)

    ownership['wrapper'] = 'unknown'
    supervisor.recover('1', worker)
    assert not driver.started
    assert supervisor.read()['issue_registry']['1']['worker_wait']['ownership'] == 'unknown'
    assert config.resolved().events_log.read_text().count('type=sub-coord-recovery-wait-expired') == 1


@pytest.mark.parametrize(('handoff','compaction','exhausted'), [
    ('valid',False,False), ('valid',True,False), ('invalid',False,False),
    ('missing',False,False), ('invalid',True,False),
    ('valid',False,True), ('valid',True,True),
])
def test_expired_wait_release_resumes_with_applicable_budget(tmp_path, monkeypatch, handoff, compaction, exhausted):
    from dataclasses import replace
    from types import SimpleNamespace
    from ai_skills import pool
    from ai_skills.outcomes import outcome_path
    from ai_skills.storage import load_json, write_json_atomic
    from pool_support import recovery_child, valid_complexity_result

    budgets = {'sub_coord_recovery_attempts':int(not compaction and exhausted),
               'sub_coord_compaction_handoffs':int(compaction and exhausted)}
    config = replace(pool_fixture(tmp_path,{1:budgets}), max_worker_wait_seconds=60,
                     max_recovery_attempts=1, max_compaction_handoffs=1)
    driver = FakePoolDriver()
    install_fake(monkeypatch, driver)
    supervisor = PoolSupervisor(config)
    assert supervisor.initialize() is None
    predecessor = supervisor.worker('1')
    supervisor._intent('1', predecessor)
    supervisor.active['1'] = predecessor
    issue_dir = predecessor.agent_run.issue_dir
    child, ledger, ownership = recovery_child(issue_dir, monkeypatch)
    write_json_atomic(issue_dir/'sub-state.json', {'phase':'complexity','in_flight_agents':[ledger],
                                                  'compaction_requested':compaction})
    write_json_atomic(issue_dir/'report.json', {'outcome':'completed','summary':'unbound coordinator result'})
    clock = SimpleNamespace(now=1000)
    monkeypatch.setattr(pool, 'time', SimpleNamespace(time=lambda:clock.now, monotonic=lambda:1))
    supervisor.recover('1', predecessor)

    ownership['wrapper'] = 'dead'
    clock.now = 1060
    result_path = child.agent_run.result_file
    if handoff != 'missing':
        payload = valid_complexity_result() if handoff == 'valid' else {'outcome':'completed'}
        write_json_atomic(result_path, payload)
    supervisor.recover('1', predecessor)
    events = config.resolved().events_log.read_text()
    assert 'type=sub-coord-recovery-wait-expired' in events and 'ownership=released' in events
    journal = load_json(outcome_path(child.state_file))
    assert journal['status'] == ('success' if handoff == 'valid' else 'failed')
    assert child.agent_run.done_file.is_file()
    if handoff != 'missing':
        assert load_json(result_path) == payload

    entry = supervisor.read()['issue_registry']['1']
    counter = 'sub_coord_compaction_handoffs' if compaction else 'sub_coord_recovery_attempts'
    if exhausted:
        assert not driver.started
        assert entry['status'] == 'blocked'
        assert entry[counter] == 1
        return
    assert len(driver.started) == 1
    assert entry[counter] == 1 and sum(entry[key] for key in budgets) == 1
    assert 'worker_wait_since' not in entry and 'worker_wait' not in entry
    context = driver.started[0].agent_run.context_file.read_text()
    reason = 'finished' if handoff == 'valid' else 'failed'
    assert f'SUB_COORD_RECOVERY_REASON=in-flight-worker-{reason}' in context
    assert 'Never rerun a phase that already has a valid result artifact.' in context
    assert events.index('type=sub-coord-recovery-wait-expired') < events.index('type=sub-coord-spawn')
    supervisor.recover('1', predecessor)
    assert len(driver.started) == 1


def test_expired_wait_recovers_once_after_prelaunch_finalization(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from ai_skills import pool
    from ai_skills.outcomes import outcome_path
    from ai_skills.storage import write_json_atomic
    from pool_support import finalize_prelaunch_child, recovery_child

    config = replace(pool_fixture(tmp_path, {1:{}}), max_worker_wait_seconds=60, max_recovery_attempts=1)
    driver = FakePoolDriver()
    install_fake(monkeypatch, driver)
    supervisor = PoolSupervisor(config)
    assert supervisor.initialize() is None
    predecessor = supervisor.worker('1')
    supervisor._intent('1', predecessor)
    supervisor.active['1'] = predecessor
    issue_dir = predecessor.agent_run.issue_dir
    child, ledger, ownership = recovery_child(issue_dir, monkeypatch, provider='missing')
    write_json_atomic(issue_dir/'sub-state.json', {'phase':'complexity','in_flight_agents':[ledger]})
    clock = SimpleNamespace(now=1000)
    monkeypatch.setattr(pool, 'time', SimpleNamespace(time=lambda:clock.now, monotonic=lambda:1))
    supervisor.recover('1', predecessor)

    clock.now += 60
    supervisor.recover('1', predecessor)
    entry = supervisor.read()['issue_registry']['1']
    assert entry['worker_wait']['expired'] and entry['worker_wait']['holding']
    assert not driver.started
    assert entry.get('sub_coord_recovery_attempts', 0) == 0

    child.agent_run.result_file.write_text('{"outcome":"completed"}\n')
    finalize_prelaunch_child(child, 'rate-limited')
    artifacts = {path:path.read_bytes() for path in (child.state_file, outcome_path(child.state_file),
        child.agent_run.result_file, child.agent_run.done_file)}
    ownership['wrapper'] = 'dead'
    supervisor.recover('1', predecessor)
    supervisor.recover('1', predecessor)

    assert len(driver.started) == 1
    entry = supervisor.read()['issue_registry']['1']
    assert entry['sub_coord_recovery_attempts'] == 1
    assert entry.get('sub_coord_compaction_handoffs', 0) == 0
    context = driver.started[0].agent_run.context_file.read_text()
    assert 'SUB_COORD_RECOVERY_REASON=in-flight-worker-failed' in context
    assert all(path.read_bytes() == old for path, old in artifacts.items())
