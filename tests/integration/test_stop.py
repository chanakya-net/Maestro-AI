from dataclasses import asdict
from pathlib import Path
import json
import sys

from ai_skills.dispatch import stop_run
from ai_skills.processes import ProcessSpec, ProcessSupervisor
from ai_skills.storage import write_json_atomic


def test_stop_reaps_verified_identity_and_refuses_mismatched_identity(tmp_path):
    supervisor=ProcessSupervisor(tmp_path/'.run-with-it'/'processes')
    process=supervisor.start(ProcessSpec((sys.executable,'-c','import time;time.sleep(20)'),tmp_path,{}),run_id='run',attempt_id='one')
    state=tmp_path/'.run-with-it'/'worker.process.json'
    record={'identity':asdict(process.identity),'record_directory':str(supervisor.directory)}
    write_json_atomic(state,record)
    assert stop_run(tmp_path,grace_seconds=.02).state=='dead'
    assert supervisor.inspect(process.identity).state=='dead'
    other=supervisor.start(ProcessSpec((sys.executable,'-c','import time;time.sleep(20)'),tmp_path,{}),run_id='run',attempt_id='two')
    try:
        record['identity']=asdict(other.identity);record['identity']['created_at']+=1
        write_json_atomic(state,record)
        assert stop_run(tmp_path,grace_seconds=.02).state=='unknown'
        assert supervisor.inspect(other.identity).state=='alive'
    finally:
        other.close()


def test_stop_requests_foreground_pool_admission_shutdown_before_workers(tmp_path):
    import os
    import socket
    import subprocess
    import time
    import psutil
    state=tmp_path/'.run-with-it'/'main'/'pool.state.json'
    state.parent.mkdir(parents=True)
    script='''import json,os,sys,time,socket
from pathlib import Path
import psutil
path=Path(sys.argv[1]); stop=Path(str(path)+'.stop')
lease={'engine':'python','state':'running','pool_pid':os.getpid(),'pool_created_at':psutil.Process().create_time(),'host':socket.gethostname()}
path.write_text(json.dumps(lease))
while not stop.exists(): time.sleep(.01)
request=json.loads(stop.read_text())
assert all(request[key]==lease[key] for key in ('pool_pid','pool_created_at','host'))
lease['state']='stopped';path.write_text(json.dumps(lease))
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(state)])
    try:
        deadline=time.monotonic()+5
        while not state.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert stop_run(tmp_path,grace_seconds=.02).state=='dead'
        process.wait(timeout=5)
        assert json.loads(state.read_text())['state']=='stopped'
        assert Path(str(state)+'.stop').is_file()
    finally:
        if process.poll() is None: process.kill();process.wait()


def test_stop_rescans_worker_admissions_after_pool_acknowledges_shutdown(tmp_path):
    import subprocess
    import time
    state=tmp_path/'pool.state.json'
    script='''import json,os,sys,time,socket
from pathlib import Path
from dataclasses import asdict
import psutil
sys.path.insert(0,sys.argv[2])
from ai_skills.processes import ProcessSupervisor,ProcessSpec
from ai_skills.storage import write_json_atomic
root=Path(sys.argv[1]);state=root/'pool.state.json';stop=Path(str(state)+'.stop')
lease={'engine':'python','state':'running','pool_pid':os.getpid(),'pool_created_at':psutil.Process().create_time(),'host':socket.gethostname()}
write_json_atomic(state,lease)
while not stop.exists():time.sleep(.01)
supervisor=ProcessSupervisor(root/'processes')
child=supervisor.start(ProcessSpec((sys.executable,'-c','import time;time.sleep(20)'),root,{}),run_id='late',attempt_id='one')
write_json_atomic(root/'late.process.json',{'identity':asdict(child.identity),'record_directory':str(supervisor.directory)})
lease['state']='stopped';write_json_atomic(state,lease)
'''
    source=str(Path(__file__).resolve().parents[2]/'src')
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path),source])
    record=None
    try:
        deadline=time.monotonic()+5
        while not state.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert stop_run(tmp_path,grace_seconds=.02).state=='dead'
        record=json.loads((tmp_path/'late.process.json').read_text())
        from ai_skills.processes import ProcessIdentity
        supervisor=ProcessSupervisor(Path(record['record_directory']))
        assert supervisor.inspect(ProcessIdentity(**record['identity'])).state=='dead'
    finally:
        if process.poll() is None:process.kill()
        process.wait(timeout=5)
        if record:
            supervisor.stop(ProcessIdentity(**record['identity']),grace_seconds=.02)


def test_stop_finds_admission_before_sidecar_publication(tmp_path):
    supervisor = ProcessSupervisor(tmp_path / '.run-with-it/processes')
    owned = supervisor.start(ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), tmp_path, {}),
                             run_id='interrupted', attempt_id='one')
    try:
        assert stop_run(tmp_path, grace_seconds=.02).state == 'dead'
        assert supervisor.inspect(owned.identity).state == 'dead'
    finally:
        owned.close(grace_seconds=.02)


def test_stop_refuses_incomplete_durable_admission(tmp_path):
    import socket
    supervisor = ProcessSupervisor(tmp_path / '.run-with-it/processes')
    write_json_atomic(supervisor._path('interrupted', 'one'), {
        'schema_version': 1, 'state': 'starting', 'host': socket.gethostname(),
        'run_id': 'interrupted', 'attempt_id': 'one'})
    assert stop_run(tmp_path, grace_seconds=.02).state == 'unknown'


def test_stop_finds_custom_intent_admission_without_stopping_unrelated_run(tmp_path):
    from pool_support import pool_fixture
    from ai_skills.pool import PoolSupervisor
    from dataclasses import replace
    root = tmp_path / 'run'; root.mkdir()
    shared = tmp_path / 'shared-processes'
    config = replace(pool_fixture(root, {1: {}}), record_directory=shared)
    pool = PoolSupervisor(config); assert pool.initialize() is None
    worker = pool.worker('1'); pool._intent('1', worker)
    supervisor = ProcessSupervisor(shared)
    owned = supervisor.start(ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), root, {}),
                             run_id=worker.agent_run.run_id, attempt_id=worker.agent_run.attempt_id)
    unrelated = supervisor.start(ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), root, {}),
                                 run_id='unrelated', attempt_id='one')
    try:
        assert stop_run(root, grace_seconds=.02).state == 'dead'
        assert supervisor.inspect(owned.identity).state == 'dead'
        assert supervisor.inspect(unrelated.identity).state == 'alive'
    finally:
        owned.close(grace_seconds=.02); unrelated.close(grace_seconds=.02)


def test_stop_finds_custom_directory_recorded_by_worker_launch_config(tmp_path):
    shared = tmp_path / 'shared-processes'
    root = tmp_path / 'run'; root.mkdir()
    supervisor = ProcessSupervisor(shared)
    owned = supervisor.start(ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), root, {}),
                             run_id='worker', attempt_id='one')
    write_json_atomic(root / 'worker.state.json.config.json', {'agent_run': {
        'run_id': 'worker', 'attempt_id': 'one', 'record_directory': str(shared)}})
    try:
        assert stop_run(root, grace_seconds=.02).state == 'dead'
        assert supervisor.inspect(owned.identity).state == 'dead'
    finally:
        owned.close(grace_seconds=.02)


def test_stop_finds_detached_pool_custom_admission_before_sidecar(tmp_path, monkeypatch):
    import pytest
    import ai_skills.pool as pool
    from dataclasses import replace
    from pool_support import pool_fixture
    root = tmp_path / 'run'; root.mkdir()
    config = replace(pool_fixture(root, {1: {}}), record_directory=tmp_path / 'shared')
    admitted = []
    real_start = ProcessSupervisor.start
    real_write = pool.write_json_atomic
    def start(supervisor, spec, **keys):
        owned = real_start(supervisor, ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), root, {}), **keys)
        admitted.append(owned)
        return owned
    def crash_before_sidecar(path, value):
        if str(path).endswith('.process.json'):
            raise RuntimeError('publication interrupted')
        real_write(path, value)
    monkeypatch.setattr(ProcessSupervisor, 'start', start)
    monkeypatch.setattr(pool, 'write_json_atomic', crash_before_sidecar)
    try:
        with pytest.raises(RuntimeError, match='publication interrupted'):
            pool.detach_pool(config)
        assert stop_run(root, grace_seconds=.02).state == 'dead'
        assert admitted[0].supervisor.inspect(admitted[0].identity).state == 'dead'
    finally:
        for owned in admitted:
            owned.close(grace_seconds=.02)


def test_stop_scopes_metadata_discovery_to_runtime_state(tmp_path):
    unrelated = tmp_path / 'node_modules' / 'fixture'; unrelated.mkdir(parents=True)
    (unrelated / 'pool.state.json').write_text('{"engine":"python","state":"running","host":"other-host"}')
    (unrelated / 'worker.state.json.config.json').write_text('{invalid project data')
    (tmp_path / 'build.config.json').write_text('{invalid project data')
    assert stop_run(tmp_path, grace_seconds=.02).state == 'dead'


def test_stop_preflights_unpublished_foreign_claim_before_stopping_owned_process(tmp_path):
    supervisor = ProcessSupervisor(tmp_path / '.run-with-it/processes')
    owned = supervisor.start(ProcessSpec((sys.executable, '-c', 'import time;time.sleep(20)'), tmp_path, {}),
                             run_id='owned', attempt_id='one')
    try:
        write_json_atomic(supervisor._path('foreign', 'one'), {
            'schema_version': 1, 'state': 'alive', 'host': 'other-host', 'run_id': 'foreign', 'attempt_id': 'one',
            'identity': {**asdict(owned.identity), 'host': 'other-host', 'run_id': 'foreign'}})
        assert stop_run(tmp_path, grace_seconds=.02).state == 'unknown'
        assert supervisor.inspect(owned.identity).state == 'alive'
    finally:
        owned.close(grace_seconds=.02)
