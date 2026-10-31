"""Regression cases for durable process ownership review feedback."""
import dataclasses
import importlib
import json
import os
import signal
import socket
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from ai_skills import processes
from ai_skills.storage import write_json_atomic


def test_owner_bounds_unchanged_writes_and_immediately_publishes_new_evidence(tmp_path, monkeypatch):
    platform = importlib.import_module('ai_skills.platforms.windows' if os.name == 'nt' else 'ai_skills.platforms.posix')
    tick = 0
    class Group:
        pid = 73101
        created_at = 1.0
        def __init__(self, *args): pass
        def members(self):
            if tick >= 45:
                return []
            child = [] if tick < 4 else [{'pid': 73102, 'created_at': 2.0 if tick < 8 else 3.0}]
            return [{'pid': self.pid, 'created_at': self.created_at}, *child]
        def return_code(self): return None if tick < 12 else 7
        def stop(self, grace): return []
        def close(self): pass
    def sleep(seconds):
        nonlocal tick
        tick += 1
    monkeypatch.setattr(platform, 'ProcessGroup', Group)
    monkeypatch.setattr(processes, 'time', SimpleNamespace(monotonic=lambda: tick * .05, time=lambda: 1000 + tick * .05, sleep=sleep))
    path = tmp_path / 'attempt.json'
    write_json_atomic(path, {'schema_version': 1, 'host': socket.gethostname(), 'run_id': 'run', 'attempt_id': 'one', 'state': 'starting',
                            'spec': {'argv': ['fake'], 'cwd': str(tmp_path), 'stdout': None, 'stderr': None}})
    writes = []
    def persist(target, record):
        write_json_atomic(target, record)
        writes.append((tick, json.loads(target.read_text())))
    monkeypatch.setattr(processes, 'write_json_atomic', persist)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        assert processes._own(path) == 0
    finally:
        for sig, handler in handlers.items(): signal.signal(sig, handler)
    # New member, reused PID lifetime, leader exit status, and tree completion
    # each become durable on the observation that discovers them.
    by_tick = dict(writes)
    assert by_tick[0]['identity']['pid'] == 73101
    assert by_tick[4]['members'][-1] == {'pid': 73102, 'created_at': 2.0}
    assert by_tick[8]['members'][-1] == {'pid': 73102, 'created_at': 3.0}
    assert by_tick[12]['return_code'] == 7
    assert by_tick[45]['state'] == 'dead'
    # 46 scans need at most four evidence changes, two heartbeats and terminal.
    assert len(writes) <= 7
    assert max(b[1]['updated_at'] - a[1]['updated_at'] for a, b in zip(writes, writes[1:])) <= 1.05


@pytest.mark.parametrize('publication', ['completed', 'missing-identity', 'corrupt', 'missing', 'foreign-identity'])
def test_start_rechecks_exited_owner_handshake_without_relaunch(tmp_path, monkeypatch, publication):
    supervisor = processes.ProcessSupervisor(tmp_path / 'records')
    path = supervisor._path('run', 'one')
    identity = {'pid': 73101, 'created_at': 1.0, 'host': socket.gethostname(), 'run_id': 'run', 'attempt_id': 'one'}
    launches = []
    class Owner:
        def __init__(self, *args, **kwargs): launches.append(self)
        def poll(self):
            if publication == 'corrupt':
                path.write_text('{broken')
            elif publication == 'missing':
                path.unlink(missing_ok=True)
            else:
                record = json.loads(path.read_text())
                record.update(state='dead', members=[], return_code=0, owner={'pid': 73102, 'created_at': 2.0})
                if publication != 'missing-identity':
                    record['identity'] = {**identity, 'attempt_id': 'other'} if publication == 'foreign-identity' else identity
                write_json_atomic(path, record)
            return 0
        def wait(self, timeout=None): return 0
    monkeypatch.setattr(processes.subprocess, 'Popen', Owner)
    spec = processes.ProcessSpec((sys.executable, '-c', 'pass'), tmp_path, {})
    if publication == 'completed':
        owned = supervisor.start(spec, run_id='run', attempt_id='one')
        assert dataclasses.asdict(owned.identity) == identity
        assert supervisor.return_code(owned.identity) == 0
    else:
        with pytest.raises((RuntimeError, ValueError, OSError)):
            supervisor.start(spec, run_id='run', attempt_id='one')
    assert len(launches) == 1


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signals')
def test_posix_graceful_shutdown_delivers_only_one_term(tmp_path):
    from ai_skills.platforms.posix import ProcessGroup
    marker = tmp_path / 'ready'
    code = '''import signal,sys,time
from pathlib import Path
end = None
def stop(sig, frame):
 global end
 if end is not None:
  raise SystemExit(2)
 end = time.monotonic() + .15
signal.signal(signal.SIGTERM, stop)
Path(sys.argv[1]).touch()
while end is None or time.monotonic() < end:
 time.sleep(.005)
'''
    with open(os.devnull, 'rb') as stdin, open(os.devnull, 'ab') as output:
        group = ProcessGroup((sys.executable, '-c', code, str(marker)), tmp_path, os.environ.copy(), stdin, output, output)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert marker.exists()
            assert group.stop(.4) == []
            assert group.return_code() == 0
        finally:
            if group.process.poll() is None:
                group.stop(0)
            group.close()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX sessions')
def test_posix_signal_phases_include_new_descendants_and_new_pid_lifetimes(monkeypatch):
    from ai_skills.platforms import posix
    tick = 0
    signals = []
    def current(session):
        if tick >= 6:
            return []
        child = [] if tick == 0 else [{'pid': 73102, 'created_at': 2.0 if tick == 1 else 3.0}]
        return [{'pid': 73101, 'created_at': 1.0}, *child]
    class Process:
        def __init__(self, pid): self.pid = pid
        def create_time(self): return 1.0 if self.pid == 73101 else 2.0 if tick == 1 else 3.0
        def status(self): return psutil.STATUS_RUNNING
        def send_signal(self, sig): signals.append((self.pid, self.create_time(), sig))
    def sleep(seconds):
        nonlocal tick
        tick += 1
    monkeypatch.setattr(posix, 'members', current)
    monkeypatch.setattr(posix.psutil, 'Process', Process)
    monkeypatch.setattr(posix.os, 'getsid', lambda pid: 73101)
    monkeypatch.setattr(posix, 'time', SimpleNamespace(monotonic=lambda: tick * .03, sleep=sleep))
    assert posix.terminate(73101, .1, [{'pid': 73101, 'created_at': 1.0}]) == []
    assert signals == [(73101, 1.0, signal.SIGTERM), (73102, 2.0, signal.SIGTERM),
                       (73102, 3.0, signal.SIGTERM), (73101, 1.0, signal.SIGKILL),
                       (73102, 3.0, signal.SIGKILL)]


def test_confirmed_stop_removes_its_matching_request(tmp_path):
    supervisor = processes.ProcessSupervisor(tmp_path / 'records')
    owned = supervisor.start(processes.ProcessSpec((sys.executable, '-c', 'import time;time.sleep(30)'), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        assert owned.close(grace_seconds=.1).state == 'dead'
        assert not owned.record_path.with_suffix('.stop').exists()
    finally:
        owned.close(grace_seconds=0)


@pytest.mark.parametrize('request_kind', ['matching', 'future-lifetime', 'foreign-attempt', 'corrupt'])
def test_dead_inspection_only_removes_request_for_confirmed_identity(tmp_path, request_kind):
    supervisor = processes.ProcessSupervisor(tmp_path / 'records')
    owned = supervisor.start(processes.ProcessSpec((sys.executable, '-c', 'pass'), tmp_path, {}), run_id='run', attempt_id='one')
    deadline = time.monotonic() + 5
    while supervisor.inspect(owned.identity).state != 'dead' and time.monotonic() < deadline:
        time.sleep(.01)
    assert supervisor.inspect(owned.identity).state == 'dead'
    request = owned.record_path.with_suffix('.stop')
    identity = dataclasses.asdict(owned.identity)
    if request_kind == 'future-lifetime': identity['created_at'] += 1
    if request_kind == 'foreign-attempt': identity['attempt_id'] = 'future'
    if request_kind == 'corrupt':
        request.write_text('{broken')
    else:
        write_json_atomic(request, {'identity': identity, 'grace_seconds': 1})
    original = request.read_bytes()
    assert owned.close(grace_seconds=0).state == 'dead'
    if request_kind == 'matching':
        assert not request.exists()
    else:
        assert request.read_bytes() == original


@pytest.mark.skipif(os.name == 'nt', reason='POSIX owner-loss reconciliation')
def test_owner_loss_stop_also_removes_only_its_confirmed_dead_request(tmp_path):
    supervisor = processes.ProcessSupervisor(tmp_path / 'records')
    owned = supervisor.start(processes.ProcessSpec((sys.executable, '-c', 'import time;time.sleep(30)'), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        owner = json.loads(owned.record_path.read_text())['owner']
        os.kill(owner['pid'], signal.SIGKILL)
        supervisor._owners[owned.record_path].wait(timeout=3)
        request = owned.record_path.with_suffix('.stop')
        write_json_atomic(request, {'identity': dataclasses.asdict(owned.identity), 'grace_seconds': .1})
        assert owned.close(grace_seconds=.1).state == 'dead'
        assert not request.exists()
    finally:
        owned.close(grace_seconds=0)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX session identity')
@pytest.mark.parametrize('observation', ['reused-anchor', 'recycled-session', 'inaccessible-anchor', 'changed-before-signal'])
def test_posix_termination_never_signals_unverified_lifetimes(monkeypatch, observation):
    from ai_skills.platforms import posix
    reads = 0
    signals = []
    class Process:
        def __init__(self, pid): self.pid = pid
        def create_time(self):
            nonlocal reads
            reads += 1
            if observation == 'inaccessible-anchor': raise psutil.AccessDenied(self.pid)
            return 2.0 if observation == 'reused-anchor' or (observation == 'changed-before-signal' and reads > 1) else 1.0
        def status(self): return psutil.STATUS_RUNNING
        def send_signal(self, sig): signals.append(sig)
    monkeypatch.setattr(posix.psutil, 'Process', Process)
    monkeypatch.setattr(posix.os, 'getsid', lambda pid: 99999 if observation == 'recycled-session' else 73101)
    monkeypatch.setattr(posix, 'members', lambda session: [{'pid': 73101, 'created_at': 1.0}])
    with pytest.raises((PermissionError, psutil.AccessDenied)):
        posix.terminate(73101, 0, [{'pid': 73101, 'created_at': 1.0}])
    assert signals == []


def test_unknown_owner_preserves_request_and_live_process(tmp_path, monkeypatch):
    supervisor = processes.ProcessSupervisor(tmp_path / 'records')
    owned = supervisor.start(processes.ProcessSpec((sys.executable, '-c', 'import time;time.sleep(30)'), tmp_path, {}), run_id='run', attempt_id='one')
    request = owned.record_path.with_suffix('.stop')
    future = dataclasses.asdict(owned.identity)
    future['created_at'] += 1
    write_json_atomic(request, {'identity': future, 'grace_seconds': .1})
    try:
        original = request.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(processes, '_match', lambda member: 'unknown')
            assert owned.close(grace_seconds=0).state == 'unknown'
        assert request.read_bytes() == original
        assert psutil.Process(owned.identity.pid).status() != psutil.STATUS_ZOMBIE
    finally:
        request.unlink(missing_ok=True)
        owned.close(grace_seconds=0)
