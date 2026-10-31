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
