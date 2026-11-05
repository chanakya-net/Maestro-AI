import json
from ai_skills.pool import PoolConfig, run_pool

def test_active_legacy_state_is_rejected_without_relaunch(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    state=tmp_path/'state.json';state.write_text(json.dumps({'active_pool_issues':['7'],'issue_registry':{'7':{'status':'in_progress','pid':42}}}))
    monkeypatch.setattr(pool,'dispatch_worker',lambda config: (_ for _ in ()).throw(AssertionError('launch')))
    result=run_pool(PoolConfig(state_file=state,poll_seconds=.01))
    assert result.state == 'legacy-engine-active'
    assert json.loads(state.read_text())['issue_registry']['7']['pid'] == 42

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from ai_skills.pool import PoolSupervisor,reconcile_attempts
from ai_skills.dispatch import WorkerResult
from ai_skills.storage import load_json, write_json_atomic
from pool_support import FakePoolDriver,pool_fixture,install_fake

class EmptyProcesses:
    def __init__(self,directory,records=None):self.directory=directory;self.records=records or {}
    def lookup_attempt(self,run_id,attempt_id):return self.records.get((run_id,attempt_id))


def prepared_intent(tmp_path):
    config=pool_fixture(tmp_path,{1:{}})
    supervisor=PoolSupervisor(config);assert supervisor.initialize() is None
    worker=supervisor.worker('1');supervisor._intent('1',worker)
    return config,supervisor,worker

def test_incomplete_intent_without_any_claim_resumes_same_attempt(tmp_path,monkeypatch):
    config,first,worker=prepared_intent(tmp_path)
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    result=run_pool(config)
    assert result.state=='pool-empty'
    assert len(driver.started)==1
    assert driver.started[0].agent_run.attempt_id==worker.agent_run.attempt_id

def test_missing_claim_with_durable_process_record_cannot_relaunch(tmp_path):
    config,supervisor,worker=prepared_intent(tmp_path)
    processes=EmptyProcesses(tmp_path,{(worker.agent_run.run_id+'-dispatcher',worker.agent_run.attempt_id):{'state':'starting'}})
    result=reconcile_attempts(supervisor.read(),processes)
    assert '1' in result.unknown
    assert not result.launchable

def test_crash_after_spawn_before_registration_adopts_only_one_attempt(tmp_path,monkeypatch):
    config,first,worker=prepared_intent(tmp_path)
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    driver.dispatch(worker)
    assert first.read()['pool_attempts']['1']['status']=='launch-intent'
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==1
    assert json.loads(Path(config.state_file).read_text())['issue_registry']['1']['status']=='completed'

def test_unknown_owner_never_resets_dirty_work_or_spawns(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config,first,worker=prepared_intent(tmp_path)
    Path(worker.state_file).parent.mkdir(parents=True,exist_ok=True)
    write_json_atomic(worker.state_file,{'state':'running'})
    dirty=tmp_path/'dirty.txt';dirty.write_text('uncommitted work')
    monkeypatch.setattr(pool,'adopt_worker',lambda path:WorkerResult('unknown',3,Path(path),'identity inaccessible'))
    monkeypatch.setattr(pool,'dispatch_worker',lambda cfg:pytest.fail('unknown owner relaunched'))
    assert run_pool(config).state=='ownership-unknown'
    assert dirty.read_text()=='uncommitted work'
    assert first.read()['pool_attempts']['1']['attempt_id']==worker.agent_run.attempt_id

def test_simultaneous_pool_startup_has_one_owner(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config=pool_fixture(tmp_path,{1:{}})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    entered=Event();release=Event();real_dispatch=driver.dispatch
    def dispatch(worker):
        entered.set();assert release.wait(5)
        return real_dispatch(worker)
    monkeypatch.setattr(pool,'dispatch_worker',dispatch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first=executor.submit(run_pool,config)
        assert entered.wait(5)
        second=run_pool(config)
        assert second.state=='pool-already-running'
        release.set()
        assert first.result(timeout=5).state=='pool-empty'
    assert len(driver.started)==1

def test_pinned_interpreter_mismatch_does_not_start_workers(tmp_path,monkeypatch):
    config=pool_fixture(tmp_path,{1:{}})
    write_json_atomic(config.resolved().run_root/'.run-with-it'/'engine.json',{'schema_version':1,'engine':'python','engine_version':1,'python':'other-python'})
    with pytest.raises(ValueError,match='pinned'):run_pool(config)

def test_targeted_stop_prevents_admission_and_preserves_intent(tmp_path,monkeypatch):
    import os,psutil
    config=pool_fixture(tmp_path,{1:{}}).resolved()
    write_json_atomic(Path(str(config.pool_state_file)+'.stop'),{'pool_pid':os.getpid(),'pool_created_at':psutil.Process().create_time(),'host':socket.gethostname()})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    assert run_pool(config).state=='stopped'
    assert not driver.started
    assert json.loads(Path(config.state_file).read_text())['issue_registry']['1']['status']=='pending'

def test_stale_stop_request_cannot_stop_new_owner(tmp_path,monkeypatch):
    import os
    config=pool_fixture(tmp_path,{1:{}}).resolved()
    write_json_atomic(Path(str(config.pool_state_file)+'.stop'),{'pool_pid':os.getpid(),'pool_created_at':1,'host':socket.gethostname()})
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert len(driver.started)==1

@pytest.mark.parametrize('registered',[False,True])
def test_real_restart_adopts_live_provider_without_second_launch(tmp_path,monkeypatch,registered):
    from pool_support import real_pool_fixture
    from ai_skills.dispatch import dispatch_worker
    monkeypatch.setenv('RUN_WITH_IT_GITHUB_UPDATES','0')
    config=real_pool_fixture(tmp_path,delay=.4)
    first=PoolSupervisor(config);assert first.initialize() is None
    worker=first.worker('1');first._intent('1',worker)
    result=dispatch_worker(worker)
    if registered:first._registered('1',worker,result)
    (tmp_path/'dirty.txt').write_text('preserve dirty work')
    assert run_pool(config).state=='pool-empty'
    assert len((tmp_path/'launches.log').read_text().splitlines())==1
    assert (tmp_path/'dirty.txt').read_text()=='preserve dirty work'


@pytest.mark.parametrize('boundary', ['outcome', 'state', 'done'])
def test_restart_adopts_interrupted_publication_without_relaunch(tmp_path, monkeypatch, boundary):
    from ai_skills import dispatch
    from pool_support import real_pool_fixture

    monkeypatch.setenv('RUN_WITH_IT_GITHUB_UPDATES', '0')
    config = real_pool_fixture(tmp_path, delay=0)
    first = PoolSupervisor(config)
    assert first.initialize() is None
    worker = replace(first.worker('1'), detach=False)
    first._intent('1', worker)
    state_file = Path(worker.state_file).resolve()
    journal = dispatch.outcome_path(state_file)
    write_json, write_text = dispatch.write_json_atomic, dispatch.write_text_atomic

    def publish_json(path, value):
        if boundary == 'outcome' and path == journal and value['status'] == 'success':
            raise OSError('injected pool publication failure')
        if boundary == 'state' and path == state_file and value.get('state') == 'completed':
            raise OSError('injected pool publication failure')
        return write_json(path, value)

    def publish_text(path, value, *, exclusive=False):
        if boundary == 'done' and path == Path(worker.agent_run.done_file).resolve():
            raise OSError('injected pool publication failure')
        return write_text(path, value, exclusive=exclusive)

    with monkeypatch.context() as patch:
        patch.setattr(dispatch, 'write_json_atomic', publish_json)
        patch.setattr(dispatch, 'write_text_atomic', publish_text)
        with pytest.raises(OSError, match='injected pool publication failure'):
            dispatch.dispatch_worker(worker)

    report = Path(worker.agent_run.result_file)
    original_report = report.read_bytes()
    journal_before = journal.read_bytes()
    assert json.loads(journal_before)['status'] == ('pending' if boundary == 'outcome' else 'success')
    assert not Path(worker.agent_run.done_file).exists()
    state = load_json(state_file)
    state['launcher_identity']['created_at'] += 1
    write_json_atomic(state_file, state)
    assert run_pool(config).state == 'pool-empty'
    saved = first.read()
    assert saved['issue_registry']['1']['status'] == 'completed'
    assert saved['pool_attempts']['1']['status'] == 'finalized'
    outcome = load_json(journal)
    assert (outcome['status'], outcome['issue_outcome']) == ('success', 'completed')
    if boundary != 'outcome':
        assert journal.read_bytes() == journal_before
    assert '|status=success|source=dispatcher|' in Path(worker.agent_run.done_file).read_text(encoding='utf-8')
    assert report.read_bytes() == original_report
    assert len((tmp_path / 'launches.log').read_text(encoding='utf-8').splitlines()) == 1


def test_real_detached_pool_preserves_options_and_worker_overrides(tmp_path,monkeypatch):
    import time
    from ai_skills.dispatch import stop_run
    from ai_skills.processes import ProcessIdentity,ProcessSupervisor
    from pool_support import real_pool_fixture
    monkeypatch.setenv('RUN_WITH_IT_GITHUB_UPDATES','0')
    config=replace(real_pool_fixture(tmp_path,delay=.2),detach=True,effort='xhigh',quiet_seconds=3,stall_seconds=7,
        max_compaction_handoffs=9,environment={**dict(__import__('os').environ),'FORCED_AGENT':'worker-selector','FORCED_MODEL':'worker-model','SECRET_TOKEN':'never persist'})
    resolved=config.resolved()
    try:
        result=run_pool(config)
        assert result.state=='detached'
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            current=load_json(Path(config.state_file))
            if current['issue_registry']['1']['status']=='completed':break
            time.sleep(.03)
        assert current['issue_registry']['1']['status']=='completed'
        record=load_json(Path(str(resolved.pool_state_file)+'.config.json'))
        assert record['effort']=='xhigh' and record['quiet_seconds']==3 and record['stall_seconds']==7
        assert record['max_compaction_handoffs']==9
        assert 'SECRET_TOKEN' not in json.dumps(record)
        launch=json.loads((tmp_path/'launches.log').read_text().splitlines()[0])
        assert launch['forced_agent']=='worker-selector' and launch['forced_model']=='worker-model'
        process=load_json(Path(str(resolved.pool_state_file)+'.process.json'))
        supervisor=ProcessSupervisor(Path(process['record_directory']));identity=ProcessIdentity(**process['identity'])
        while time.monotonic()<deadline and supervisor.inspect(identity).state!='dead':time.sleep(.03)
        assert supervisor.inspect(identity).state=='dead'
    finally:
        stop_run(tmp_path)

def test_completion_and_attempt_terminal_state_survive_crash_together(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'completed','summary':'finished'})
    original=pool.state_policy.finalize_issue
    class SimulatedCrash(RuntimeError):pass
    def crash_after_state_commit(args):
        original(args)
        raise SimulatedCrash('crash after atomic finalization')
    monkeypatch.setattr(pool.state_policy,'finalize_issue',crash_after_state_commit)
    with pytest.raises(SimulatedCrash):supervisor.finalize('1',worker)
    saved=supervisor.read()
    assert saved['issue_registry']['1']['status']=='completed'
    assert saved['pool_attempts']['1']['status']=='finalized'
    assert saved['active_pool_issues']==[]

def test_restart_resumes_merge_recovery_after_atomic_issue_finalization(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from ai_skills import state
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'merge_failed','summary':'merge conflict'})
    state.finalize_issue(SimpleNamespace(state_file=str(config.state_file),issue='1',report_file=str(worker.agent_run.result_file),pool_attempt_id=worker.agent_run.attempt_id,quiet=True))
    driver=FakePoolDriver();install_fake(monkeypatch,driver)
    assert run_pool(config).state=='pool-empty'
    assert [item.agent_run.role for item in driver.started]==['merge-recovery']
    assert supervisor.read()['issue_registry']['1']['status']=='completed'

def test_stale_attempt_cannot_finalize_a_new_attempt(tmp_path):
    from types import SimpleNamespace
    from ai_skills import state
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'completed'})
    before=Path(config.state_file).read_bytes()
    with pytest.raises(ValueError,match='stale pool attempt'):
        state.finalize_issue(SimpleNamespace(state_file=str(config.state_file),issue='1',report_file=str(worker.agent_run.result_file),pool_attempt_id='foreign',quiet=True))
    assert Path(config.state_file).read_bytes()==before

def test_discovered_inaccessible_process_identity_is_never_adopted_or_replaced(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config,supervisor,worker=prepared_intent(tmp_path)
    Path(worker.state_file).parent.mkdir(parents=True,exist_ok=True)
    write_json_atomic(worker.state_file,{'state':'running'})
    raw={'identity':{'pid':999999,'created_at':1,'host':socket.gethostname(),'run_id':worker.agent_run.run_id,'attempt_id':worker.agent_run.attempt_id}}
    processes=EmptyProcesses(tmp_path,{(worker.agent_run.run_id,worker.agent_run.attempt_id):raw})
    processes.inspect=lambda identity:SimpleNamespace(state='unknown')
    monkeypatch.setattr(pool,'adopt_worker',lambda path:pytest.fail('inaccessible identity was adopted'))
    result=reconcile_attempts(supervisor.read(),processes)
    assert '1' in result.unknown and not result.launchable
    assert not Path(str(worker.state_file)+'.process.json').exists()


def test_intent_only_restart_restores_persisted_worker_overrides_and_current_environment(tmp_path, monkeypatch):
    from ai_skills.pool import PoolConfig
    from pool_support import real_pool_fixture
    monkeypatch.delenv('FORCED_AGENT', raising=False)
    monkeypatch.delenv('FORCED_MODEL', raising=False)
    monkeypatch.setenv('RESUME_EXECUTION_SECRET', 'pre-crash-secret')
    config = replace(real_pool_fixture(tmp_path), forced_agent='explicit-worker', forced_model='explicit-model')
    script = tmp_path / 'provider.py'
    script.write_text(script.read_text().replace("'forced_agent':", "'run_root':os.environ.get('AI_SKILLS_RUN_ROOT'),'current_env':os.environ.get('RESUME_EXECUTION_SECRET'),'forced_agent':"))
    first = PoolSupervisor(config); assert first.initialize() is None
    worker = first.worker('1'); first._intent('1', worker)
    saved = first.config.to_record()
    assert 'pre-crash-secret' not in json.dumps(saved)
    assert 'pre-crash-secret' not in Path(config.state_file).read_text()
    monkeypatch.setenv('RESUME_EXECUTION_SECRET', 'current-secret-only')
    assert run_pool(PoolConfig.from_record(saved)).state == 'pool-empty'
    launches = [json.loads(line) for line in (tmp_path / 'launches.log').read_text().splitlines()]
    assert len(launches) == 1
    assert launches[0]['forced_agent'] == 'explicit-worker'
    assert launches[0]['forced_model'] == 'explicit-model'
    assert launches[0]['run_root'] == str(tmp_path)
    assert launches[0]['current_env'] == 'current-secret-only'
    assert 'current-secret-only' not in Path(config.state_file).read_text()


def test_claim_only_restart_finalizes_through_bound_outcome(tmp_path):
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(worker.state_file,{'state':'launching','run_id':worker.agent_run.run_id,'attempt_id':worker.agent_run.attempt_id,
        'launcher_identity':{'pid':999999999,'created_at':1,'host':socket.gethostname()}})
    result=reconcile_attempts(supervisor.read(),EmptyProcesses(tmp_path))
    assert result.finished['1'].reason=='dispatch-bootstrap-failed'
    outcome=load_json(Path(str(worker.state_file)+'.outcome.json'))
    assert outcome['status']=='failed' and outcome['provider_return_code'] is None
    assert outcome['worker_return_code']==1
    assert 'status=failed' in Path(worker.agent_run.done_file).read_text()


def test_terminal_state_without_provider_proof_is_not_pool_completion(tmp_path):
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(worker.state_file,{'state':'completed','return_code':0})
    result=reconcile_attempts(supervisor.read(),EmptyProcesses(tmp_path))
    assert '1' in result.unknown and not result.finished and not result.launchable


@pytest.mark.parametrize('stage',['claim','config','pending'])
def test_dead_launcher_reconciles_each_prelaunch_publication_stage(tmp_path,stage):
    from ai_skills.outcomes import pending_outcome
    from ai_skills.dispatch import expected_artifact_paths
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(worker.state_file,{'state':'launching','run_id':worker.agent_run.run_id,'attempt_id':worker.agent_run.attempt_id,
        'launcher_identity':{'pid':999999999,'created_at':1,'host':socket.gethostname()}})
    if stage!='claim':write_json_atomic(Path(str(worker.state_file)+'.config.json'),worker.to_record())
    if stage=='pending':
        run=worker.agent_run
        write_json_atomic(Path(str(worker.state_file)+'.outcome.json'),pending_outcome(run_id=run.run_id,attempt_id=run.attempt_id,
            issue=run.issue,role=run.role,state_file=worker.state_file,expected_artifacts=expected_artifact_paths(worker)))
    result=reconcile_attempts(supervisor.read(),EmptyProcesses(tmp_path))
    assert not result.launchable and not result.unknown and not result.active
    assert (result.finished['1'].state,result.finished['1'].return_code,result.finished['1'].reason)==('failed',1,'dispatch-bootstrap-failed')
    assert load_json(Path(str(worker.state_file)+'.outcome.json'))['status']=='failed'


def test_unknown_launcher_reconciliation_never_finalizes_or_relaunches(tmp_path):
    config,supervisor,worker=prepared_intent(tmp_path)
    write_json_atomic(worker.state_file,{'state':'launching','run_id':worker.agent_run.run_id,'attempt_id':worker.agent_run.attempt_id,
        'launcher_identity':{'pid':999999999,'created_at':1,'host':'unreachable-host'}})
    result=reconcile_attempts(supervisor.read(),EmptyProcesses(tmp_path))
    assert '1' in result.unknown and not result.finished and not result.launchable
    assert not Path(str(worker.state_file)+'.outcome.json').exists()


class PoolTransitionCrash(RuntimeError):
    pass


def real_failed_pool_worker(tmp_path, monkeypatch, *, role='sub-coord', allow_recovery=False):
    from ai_skills.dispatch import dispatch_worker
    from pool_support import real_pool_fixture
    import ai_skills.pool as pool
    config=replace(real_pool_fixture(tmp_path,delay=0),max_recovery_attempts=2 if allow_recovery else 0)
    script=tmp_path/'provider.py'
    source=script.read_text()
    start=source.index('result.write_text(json.dumps(')
    end=source.index("\nprint('STATUS",start)
    replacement="""report={'outcome':'completed','summary':'recovered fixture','verification':{'passed':True}}
if not recovery:
    report={'schema_version':1,'artifact_kind':'role_failure','run_id':os.environ['RUN_WITH_IT_RUN_ID'],'attempt_id':os.environ['RUN_WITH_IT_ATTEMPT_ID'],'issue':issue,'role':role,'status':'blocked','reason':'verification-unavailable','summary':'External verification is unavailable','verification':{'passed':False}}
    (Path(os.environ['RUN_WITH_IT_ISSUE_DIR'])/'sub-state.json').write_text(json.dumps({'phase':'impl','in_flight_agents':[]}))
result.write_text(json.dumps(report))"""
    script.write_text((source[:start]+replacement+source[end:]).replace("if not recovery:\n    report=", "if not recovery or role=='merge-recovery':\n    report="))
    supervisor=PoolSupervisor(config);assert supervisor.initialize() is None
    worker=replace(supervisor.worker('1',kind=role),detach=False)
    supervisor._intent('1',worker)
    result=dispatch_worker(worker)
    assert result.state=='failed' and result.return_code==1
    supervisor._registered('1',worker,result)
    monkeypatch.setattr(pool,'update_github',lambda request:None)
    return config,supervisor,worker


@pytest.mark.parametrize('role',['sub-coord','merge-recovery'])
@pytest.mark.parametrize('stage',['report-write','report-published','finalize-before','finalize-after'])
def test_real_failed_handoff_policy_transition_restarts_without_rebinding_artifact(tmp_path,monkeypatch,role,stage):
    import ai_skills.pool as pool
    from ai_skills.dispatch import adopt_worker
    config,first,worker=real_failed_pool_worker(tmp_path,monkeypatch,role=role)
    original=Path(worker.agent_run.result_file).read_bytes()
    original_journal=Path(str(worker.state_file)+'.outcome.json').read_bytes()
    with monkeypatch.context() as patch:
        if stage.startswith('report'):
            actual=pool.write_json_atomic
            def interrupted(path,payload):
                if isinstance(payload,dict) and payload.get('summary') in ('Sub-coordinator recovery could not continue','Merge recovery report handoff failed'):
                    if stage=='report-published':actual(path,payload)
                    raise OSError('injected policy report storage boundary')
                return actual(path,payload)
            patch.setattr(pool,'write_json_atomic',interrupted)
        else:
            name='finalize_issue' if role=='sub-coord' else 'finalize_merge_recovery'
            actual=getattr(pool.state_policy,name)
            def interrupted(args):
                if stage=='finalize-after':actual(args)
                raise PoolTransitionCrash('injected pool finalization boundary')
            patch.setattr(pool.state_policy,name,interrupted)
        with pytest.raises((OSError,PoolTransitionCrash)):
            first.tick()
    assert Path(worker.agent_run.result_file).read_bytes()==original
    assert Path(str(worker.state_file)+'.outcome.json').read_bytes()==original_journal
    assert adopt_worker(worker.state_file).reason=='verification-unavailable'
    assert run_pool(config).state=='pool-empty'
    saved=first.read()
    assert saved['issue_registry']['1']['status']=='blocked'
    assert saved['pool_attempts']['1']['status']=='finalized'
    assert len((tmp_path/'launches.log').read_text().splitlines())==1
    selected=saved['issue_registry']['1']['report_file']
    assert selected != str(worker.agent_run.result_file)
    assert json.loads(Path(selected).read_text())['outcome']=='blocked'


@pytest.mark.parametrize('stage',['intent-before','intent-after','quarantine-after','quarantine-storage'])
def test_real_recovery_intent_precedes_shared_quarantine_and_restarts_once(tmp_path,monkeypatch,stage):
    import ai_skills.pool as pool
    from ai_skills.dispatch import adopt_worker
    config,first,worker=real_failed_pool_worker(tmp_path,monkeypatch,allow_recovery=True)
    original=Path(worker.agent_run.result_file).read_bytes()
    with monkeypatch.context() as patch:
        if stage.startswith('intent'):
            actual=first._intent
            def interrupted(issue,next_worker):
                if stage=='intent-after':actual(issue,next_worker)
                raise PoolTransitionCrash('injected next intent boundary')
            patch.setattr(first,'_intent',interrupted)
        else:
            actual=pool.state_policy.quarantine_artifacts
            def interrupted(*args):
                if stage=='quarantine-storage':return None
                actual(*args)
                raise PoolTransitionCrash('injected quarantine boundary')
            patch.setattr(pool.state_policy,'quarantine_artifacts',interrupted)
        with pytest.raises((OSError,PoolTransitionCrash)):
            first.tick()
    saved=first.read()
    if stage=='intent-before':
        assert saved['pool_attempts']['1']['attempt_id']==worker.agent_run.attempt_id
        assert Path(worker.agent_run.result_file).read_bytes()==original
        assert adopt_worker(worker.state_file).reason=='verification-unavailable'
    else:
        assert saved['pool_attempts']['1']['attempt_id']!=worker.agent_run.attempt_id
        assert saved['pool_attempts']['1']['status']=='launch-intent'
    assert len((tmp_path/'launches.log').read_text().splitlines())==1
    assert run_pool(config).state=='pool-empty'
    assert first.read()['issue_registry']['1']['status']=='completed'
    assert len((tmp_path/'launches.log').read_text().splitlines())==2
    reports=list(Path(worker.agent_run.issue_dir).glob('recovery/predispatch-*/report.json'))
    assert reports and any(path.read_bytes()==original for path in reports)


def test_pending_policy_report_never_overrides_unknown_worker_journal(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config,first,worker=real_failed_pool_worker(tmp_path,monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(first,'finalize',lambda *args,**kwargs:(_ for _ in ()).throw(PoolTransitionCrash('before policy finalization')))
        with pytest.raises(PoolTransitionCrash):first.tick()
    assert Path(str(worker.state_file)+'.policy-report.json').exists()
    journal_path=Path(str(worker.state_file)+'.outcome.json')
    journal=json.loads(journal_path.read_text());journal['attempt_id']='foreign'
    write_json_atomic(journal_path,journal)
    before=journal_path.read_bytes()
    assert run_pool(config).state=='ownership-unknown'
    assert journal_path.read_bytes()==before
    assert first.read()['issue_registry']['1']['status']=='in_progress'
    assert len((tmp_path/'launches.log').read_text().splitlines())==1


@pytest.mark.parametrize('boundary', ['result', 'outcome', 'state', 'done'])
def test_stream_capture_restart_keeps_original_attempt(tmp_path, monkeypatch, boundary):
    from ai_skills import dispatch, runner
    from ai_skills.git import current_head
    from test_dispatch import CLAUDE_CAPTURE, stream_capture_worker

    config = stream_capture_worker(tmp_path, monkeypatch, 'claude', CLAUDE_CAPTURE, role='modify')
    state_file = config.state_file.resolve()
    journal = dispatch.outcome_path(state_file)
    run = config.agent_run
    write_json, write_text = dispatch.write_json_atomic, dispatch.write_text_atomic

    def publish_json(path, value):
        if ((boundary == 'outcome' and path == journal and value.get('status') == 'success') or
                (boundary == 'state' and path == state_file and value.get('state') == 'completed')):
            raise OSError('injected publication failure')
        return write_json(path, value)

    def publish_text(path, value, *, exclusive=False):
        if ((boundary == 'result' and path == run.result_file and exclusive) or
                (boundary == 'done' and path == run.done_file)):
            raise OSError('injected publication failure')
        return write_text(path, value, exclusive=exclusive)

    with monkeypatch.context() as patch:
        patch.setattr(dispatch, 'write_json_atomic', publish_json)
        patch.setattr(dispatch, 'write_text_atomic', publish_text)
        with pytest.raises(OSError, match='injected publication failure'):
            dispatch.dispatch_worker(config)

    head = current_head(str(run.repo_root))
    initial_outcome = load_json(journal)
    original_report = run.result_file.read_bytes() if run.result_file.exists() else None
    state = load_json(state_file)
    state['launcher_identity']['created_at'] += 1
    write_json_atomic(state_file, state)

    def forbid_launch(*args, **kwargs):
        raise AssertionError('adoption must not launch or probe again')

    monkeypatch.setattr(dispatch, 'run_agent', forbid_launch)
    monkeypatch.setattr(dispatch, 'supports_final_response_capture', forbid_launch)
    monkeypatch.setattr(runner, 'resolve_executable', forbid_launch)
    assert dispatch.adopt_worker(state_file).state == 'completed'
    report, outcome, done = (path.read_bytes() for path in (run.result_file, journal, run.done_file))
    assert dispatch.adopt_worker(state_file).state == 'completed'
    assert (report, outcome, done) == tuple(path.read_bytes() for path in (run.result_file, journal, run.done_file))
    if original_report is not None:
        assert report == original_report
    assert json.loads(report)['commit_sha'] == head
    assert [entry['id'] for entry in json.loads(report)['review_comment_closure']] == ['R001', 'R002']
    assert all(json.loads(outcome)[key] == initial_outcome[key] for key in ('run_id', 'attempt_id'))
    assert (tmp_path / 'calls').read_text(encoding='utf-8') == 'run\n'
