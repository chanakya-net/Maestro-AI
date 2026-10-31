import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

import psutil
import pytest

FIXTURE = Path(__file__).parents[1] / 'fixtures/process_fixture.py'


def api():
    assert importlib.util.find_spec('ai_skills.processes') is not None, 'process supervision foundation is missing'
    from ai_skills.processes import ProcessSpec, ProcessSupervisor
    return ProcessSpec, ProcessSupervisor


def wait_for(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError('fixture condition timed out')


def alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.parametrize('mode', ['tree', 'exit-leader', pytest.param('group-tree', marks=pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')), pytest.param('group-exit-leader', marks=pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups'))])
def test_owned_descendants_stop_even_after_leader_exit(tmp_path, mode):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    marker = tmp_path / 'tree.json'
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), mode, str(marker)), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        wait_for(marker.exists)
        descendants = json.loads(marker.read_text())
        wait_for(lambda: Path(str(marker) + '.leaf').exists())
        record = json.loads(owned.record_path.read_text())
        assert record['schema_version'] == 1
        assert record['identity']['attempt_id'] == 'one'
        if mode.endswith('exit-leader'):
            wait_for(lambda: not alive(owned.identity.pid))
        wait_for(lambda: supervisor.inspect(owned.identity).state == 'alive')
        result = supervisor.stop(owned.identity, grace_seconds=.15)
        assert result.state == 'dead'
        assert result.remaining == ()
        wait_for(lambda: not alive(descendants['pid']))
        wait_for(lambda: not alive(descendants['child']))
    finally:
        owned.close()


@pytest.mark.parametrize('field,value', [('created_at', 1.0), ('host', 'foreign-host'), ('run_id', 'wrong'), ('attempt_id', 'wrong')])
def test_refuses_stale_or_foreign_identity(tmp_path, field, value):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), 'leaf', str(tmp_path / 'ready')), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        invalid = dataclasses.replace(owned.identity, **{field: value})
        assert supervisor.inspect(invalid).state == 'unknown'
        assert supervisor.stop(invalid, grace_seconds=.05).state == 'unknown'
        assert alive(owned.identity.pid)
    finally:
        owned.close()


def test_startup_failure_is_durable(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    with pytest.raises(RuntimeError, match='launch'):
        supervisor.start(Spec((str(tmp_path / 'missing-program'),), tmp_path, {}), run_id='run', attempt_id='one')
    records = list((tmp_path / 'records').glob('*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text())['state'] == 'failed'


def test_unknown_record_never_authorizes_duplicate_attempt(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    spec = Spec((sys.executable, str(FIXTURE), 'leaf', str(tmp_path / 'ready')), tmp_path, {})
    owned = supervisor.start(spec, run_id='run', attempt_id='one')
    try:
        with pytest.raises(RuntimeError, match='existing'):
            supervisor.start(spec, run_id='run', attempt_id='one')
        record = json.loads(owned.record_path.read_text())
        owned.record_path.write_text('{broken')
        assert supervisor.inspect(owned.identity).state == 'unknown'
        with pytest.raises(RuntimeError, match='existing'):
            supervisor.start(spec, run_id='run', attempt_id='one')
        owned.record_path.write_text(json.dumps(record))
    finally:
        owned.close()


def test_stream_files_and_child_environment(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    out, err = tmp_path / 'out', tmp_path / 'err'
    code = "import os,sys,time;print(os.getenv('PROCESS_TEST'),flush=True);print('error',file=sys.stderr,flush=True);time.sleep(1)"
    owned = supervisor.start(Spec((sys.executable, '-c', code), tmp_path, {'PROCESS_TEST': 'child'}, stdout=out, stderr=err), run_id='run', attempt_id='one')
    try:
        wait_for(lambda: out.exists() and 'child' in out.read_text())
        assert err.read_text() == 'error\n'
        assert os.getenv('PROCESS_TEST') is None
    finally:
        owned.close()


def test_inaccessible_identity_is_unknown_and_does_not_kill(tmp_path, monkeypatch):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), 'leaf', str(tmp_path / 'ready')), tmp_path, {}), run_id='run', attempt_id='one')
    import ai_skills.processes as processes
    original = psutil.Process
    owner_pid = json.loads(owned.record_path.read_text())['owner']['pid']
    def inaccessible(pid=None):
        if pid == owner_pid:
            raise psutil.AccessDenied(pid)
        return original(pid)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(processes.psutil, 'Process', inaccessible)
            assert supervisor.inspect(owned.identity).state == 'unknown'
            assert supervisor.stop(owned.identity, grace_seconds=0).state == 'unknown'
        assert alive(owned.identity.pid)
    finally:
        owned.close()


def test_context_interrupt_stops_owned_process(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    with pytest.raises(KeyboardInterrupt):
        with supervisor.start(Spec((sys.executable, str(FIXTURE), 'leaf', str(tmp_path / 'ready')), tmp_path, {}), run_id='run', attempt_id='one') as owned:
            raise KeyboardInterrupt
    assert supervisor.inspect(owned.identity).state == 'dead'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX owner signal handling')
def test_ctrl_c_owner_cleans_its_session(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), 'tree', str(tmp_path / 'ready')), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        wait_for((tmp_path / 'ready').exists)
        owner_pid = json.loads(owned.record_path.read_text())['owner']['pid']
        os.kill(owner_pid, signal.SIGINT)
        wait_for(lambda: supervisor.inspect(owned.identity).state == 'dead')
    finally:
        owned.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows Job Objects')
def test_windows_suspended_assignment_prevents_early_descendants(tmp_path, monkeypatch):
    from ai_skills.platforms.windows import ProcessGroup
    import win32job
    marker = tmp_path / 'marker'
    original = win32job.AssignProcessToJobObject
    observations = []
    def assign(job, process):
        time.sleep(.2)
        assert not marker.exists(), 'child executed before ownership was established'
        original(job, process)
        observations.append(True)
    monkeypatch.setattr(win32job, 'AssignProcessToJobObject', assign)
    with open(os.devnull, 'rb') as stdin, open(os.devnull, 'ab') as output:
        group = ProcessGroup((sys.executable, str(FIXTURE), 'tree', str(marker)), tmp_path, os.environ.copy(), stdin, output, output)
        try:
            wait_for(marker.exists)
            wait_for(lambda: len(group.members()) >= 3)
            assert observations == [True]
            assert group.stop(0) == []
        finally:
            group.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows Job Objects')
def test_windows_assignment_failure_never_executes_child(tmp_path, monkeypatch):
    from ai_skills.platforms.windows import ProcessGroup
    import win32job
    marker = tmp_path / 'marker'
    def reject(job, process):
        raise OSError('assignment denied')
    monkeypatch.setattr(win32job, 'AssignProcessToJobObject', reject)
    with open(os.devnull, 'rb') as stdin, open(os.devnull, 'ab') as output:
        with pytest.raises(OSError, match='assignment denied'):
            ProcessGroup((sys.executable, str(FIXTURE), 'leaf', str(marker)), tmp_path, os.environ.copy(), stdin, output, output)
    time.sleep(.1)
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows nested Job Objects')
def test_windows_launch_inside_existing_job(tmp_path):
    import subprocess
    code = '''import os,sys,uuid
from pathlib import Path
import win32api,win32job
from ai_skills.processes import ProcessSpec,ProcessSupervisor
job=win32job.CreateJobObject(None,'ai-skills-test-'+uuid.uuid4().hex)
win32job.AssignProcessToJobObject(job,win32api.GetCurrentProcess())
s=ProcessSupervisor(Path(sys.argv[1]))
with s.start(ProcessSpec((sys.executable,sys.argv[2],'leaf',str(Path(sys.argv[1])/'ready')),Path(sys.argv[1]),{}),run_id='run',attempt_id='nested') as p:
 assert s.inspect(p.identity).state=='alive'
assert s.inspect(p.identity).state=='dead'
job.Close()
'''
    subprocess.run([sys.executable, '-c', code, str(tmp_path), str(FIXTURE)], check=True, timeout=20)


def test_environment_secrets_are_not_written_to_process_records(tmp_path, monkeypatch):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    import ai_skills.processes as processes
    original = processes.subprocess.Popen
    def launch(*args, **kwargs):
        for path in (tmp_path / 'records').glob('*.json'):
            assert 'never-persist-this-token' not in path.read_text()
            assert 'SECRET_TOKEN' not in path.read_text()
        return original(*args, **kwargs)
    monkeypatch.setattr(processes.subprocess, 'Popen', launch)
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), 'leaf', str(tmp_path / 'ready')), tmp_path, {'SECRET_TOKEN': 'never-persist-this-token'}), run_id='run', attempt_id='one')
    try:
        assert 'never-persist-this-token' not in owned.record_path.read_text()
        assert 'SECRET_TOKEN' not in owned.record_path.read_text()
    finally:
        owned.close()


def test_immediate_exit_still_returns_a_durable_identity(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    for attempt in range(5):
        owned = supervisor.start(Spec((sys.executable, '-c', 'pass'), tmp_path, {}), run_id='run', attempt_id=str(attempt))
        wait_for(lambda: supervisor.inspect(owned.identity).state == 'dead')


def test_natural_exit_reaps_owned_supervisor(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    owned = supervisor.start(Spec((sys.executable, '-c', 'import time;time.sleep(.15)'), tmp_path, {}), run_id='run', attempt_id='one')
    owner_pid = json.loads(owned.record_path.read_text())['owner']['pid']
    wait_for(lambda: supervisor.inspect(owned.identity).state == 'dead')
    assert not psutil.pid_exists(owner_pid), 'completed owner was not reaped'


@pytest.mark.parametrize('code', [0, 7])
def test_return_code_is_durable_for_restarted_supervisor(tmp_path, code):
    Spec, Supervisor = api()
    directory = tmp_path / 'records'
    supervisor = Supervisor(directory)
    owned = supervisor.start(Spec((sys.executable, '-c', f'raise SystemExit({code})'), tmp_path, {}), run_id='run', attempt_id='exit')
    wait_for(lambda: supervisor.inspect(owned.identity).state == 'dead')
    restarted = Supervisor(directory)
    wait_for(lambda: restarted.return_code(owned.identity) is not None)
    assert restarted.return_code(owned.identity) == code


def test_attempt_lookup_distinguishes_missing_from_corrupt(tmp_path):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path)
    assert supervisor.lookup_attempt('run', 'missing') is None
    owned = supervisor.start(Spec((sys.executable, '-c', 'pass'), tmp_path, {}), run_id='run', attempt_id='known')
    wait_for(lambda: supervisor.inspect(owned.identity).state == 'dead')
    assert supervisor.lookup_attempt('run', 'known')['identity']['pid'] == owned.identity.pid
    owned.record_path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        supervisor.lookup_attempt('run', 'known')


@pytest.mark.parametrize('exit_code', [0, 259, None])
@pytest.mark.parametrize('stopping', [False, True])
def test_owner_publishes_terminal_exit_after_empty_tree_or_fails_bounded(tmp_path, monkeypatch, exit_code, stopping):
    import importlib
    import socket
    from types import SimpleNamespace
    from ai_skills import processes
    from ai_skills.storage import write_json_atomic
    platform = importlib.import_module('ai_skills.platforms.windows' if os.name == 'nt' else 'ai_skills.platforms.posix')
    identity = {'pid': os.getpid(), 'created_at': psutil.Process().create_time(), 'host': socket.gethostname(),
                'run_id': 'run', 'attempt_id': 'one'}
    class ExitingGroup:
        def __init__(self, *args):
            self.pid = identity['pid']; self.created_at = identity['created_at']; self.polls = 0
        def members(self):
            return [{'pid': self.pid, 'created_at': self.created_at}] if stopping else []
        def return_code(self):
            self.polls += 1
            return None if self.polls <= 2 else exit_code
        def stop(self, grace): return []
        def close(self): pass
    ticks = (number / 100 for number in range(1000))
    monkeypatch.setattr(platform, 'ProcessGroup', ExitingGroup)
    monkeypatch.setattr(processes, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda seconds: None, time=time.time))
    path = tmp_path / 'admission.json'
    write_json_atomic(path, {'schema_version': 1, 'run_id': 'run', 'attempt_id': 'one', 'host': identity['host'],
                            'state': 'starting', 'spec': {'argv': ['fake'], 'cwd': str(tmp_path), 'stdout': None, 'stderr': None}})
    if stopping:
        write_json_atomic(path.with_suffix('.stop'), {'identity': identity, 'grace_seconds': 0})
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = processes._own(path)
    finally:
        for sig, handler in handlers.items(): signal.signal(sig, handler)
    record = json.loads(path.read_text())
    if exit_code is None:
        assert result == 1
        assert record['state'] == 'failed'
        assert 'exit status' in record['error']
    else:
        assert result == 0
        assert record['state'] == 'dead'
        assert record['return_code'] == exit_code
