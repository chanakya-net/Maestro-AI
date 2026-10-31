import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2]/"src"

from ai_skills.events import EventBus, emit_status


def test_status_fields_cannot_inject_records():
    assert emit_status('heartbeat', {'issue': '5\r\nSTATUS|bad=x', 'role': ''}) == 'STATUS|type=heartbeat|issue=5  STATUS/bad=x|role=unknown'


def test_bus_forwards_status_and_suppresses_only_heartbeat_console(tmp_path):
    bus = EventBus(status_file=tmp_path/'status', events_log=tmp_path/'events', log_file=tmp_path/'log')
    bus.forward('STATUS|type=heartbeat|issue=5', 'stdout')
    bus.forward('ROUTE|agent=codex', 'stderr')
    assert (tmp_path/'status').read_text() == 'ROUTE|agent=codex\n'
    assert (tmp_path/'events').read_text().splitlines() == ['STATUS|type=heartbeat|issue=5', 'ROUTE|agent=codex']
    assert (tmp_path/'log').read_text().splitlines() == ['STATUS|type=heartbeat|issue=5', 'ROUTE|agent=codex']


def test_final_unterminated_status_is_forwarded_and_log_records_stay_separate(tmp_path, capsys):
    bus = EventBus(events_log=tmp_path/'events',log_file=tmp_path/'log')
    bus.forward('STATUS|type=heartbeat|progress=last', 'stdout',newline=False)
    bus.emit('agent-complete',{'status':'success'})
    assert (tmp_path/'events').read_text().splitlines() == [
        'STATUS|type=heartbeat|progress=last','STATUS|type=agent-complete|status=success']
    assert (tmp_path/'log').read_text().splitlines() == [
        'STATUS|type=heartbeat|progress=last','STATUS|type=agent-complete|status=success']
    assert 'heartbeat' not in capsys.readouterr().out


def test_concurrent_process_event_records_do_not_interleave(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root=str(Path(__file__).resolve().parents[2]/'src')
    source='''import sys
sys.path.insert(0,sys.argv[1])
from ai_skills.events import EventBus
bus=EventBus(events_log=sys.argv[2])
for index in range(8): bus.emit('fixture', {'writer':sys.argv[3], 'payload':'x'*12000, 'index':index})
'''
    processes=[subprocess.Popen([sys.executable,'-c',source,root,str(tmp_path/'events'),str(writer)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for writer in range(4)]
    assert [process.wait(timeout=15) for process in processes] == [0]*4
    lines=(tmp_path/'events').read_text().splitlines()
    assert len(lines)==32
    assert all(line.count('STATUS|')==1 and '|payload='+'x'*12000+'|index=' in line for line in lines)


def test_portable_emit_cli_persists_coordinator_status_once(tmp_path):
    import json
    from ai_skills.dispatch import main
    fields=tmp_path/'fields.json';fields.write_text(json.dumps({'issue':'7','role':'sub-coord','status':'running'}))
    assert main(['emit','--type','phase','--fields-file',str(fields),'--log-file',str(tmp_path/'log'),
                 '--status-file',str(tmp_path/'status'),'--events-log',str(tmp_path/'events')])==0
    expected='STATUS|type=phase|issue=7|role=sub-coord|status=running\n'
    assert (tmp_path/'log').read_text()==expected
    assert (tmp_path/'status').read_text()==expected
    assert (tmp_path/'events').read_text()==expected


@pytest.mark.parametrize('legacy_owner', [None, '{invalid owner metadata'])
def test_abandoned_legacy_directory_lock_does_not_poison_events(tmp_path, legacy_owner):
    """Interrupted mkdir/publication/removal cannot strand the append channel."""
    from ai_skills.storage import DirectoryLock, LockTimeoutError
    events = tmp_path/'events'
    legacy = tmp_path/'events.lock'
    legacy.mkdir()
    if legacy_owner is not None:
        (legacy/'owner.json').write_text(legacy_owner, encoding='utf-8')
    source = '''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ai_skills.events import EventBus
EventBus(events_log=Path(sys.argv[2])).status('STATUS|type=recovered')
'''
    subprocess.run([sys.executable, '-c', source, str(SOURCE_ROOT), str(events)],
                   check=True, timeout=15, capture_output=True, text=True)
    assert events.read_text() == 'STATUS|type=recovered\n'
    # Event recovery must not weaken authoritative directory-lock ownership.
    with pytest.raises(LockTimeoutError):
        with DirectoryLock(events, timeout_seconds=0):
            raise AssertionError('unknown state lock was reclaimed')
    assert legacy.is_dir()


def _event_lock_holder(events, ready):
    source = '''import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ai_skills.events import _EventLock
from ai_skills.storage import write_json_atomic
with _EventLock(Path(sys.argv[2])) as lock:
    write_json_atomic(Path(sys.argv[3]), {'inheritable':os.get_inheritable(lock.descriptor)})
    while True: time.sleep(1)
'''
    return subprocess.Popen([sys.executable, '-c', source, str(SOURCE_ROOT), str(events), str(ready)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def _wait_ready(process, ready):
    deadline = time.monotonic()+15
    while not ready.exists():
        if process.poll() is not None:
            raise AssertionError(process.stderr.read())
        if time.monotonic() >= deadline:
            raise AssertionError('event lock holder did not acknowledge ownership')
        time.sleep(.01)


def test_killed_event_lock_holder_releases_kernel_lock_and_retains_sentinel(tmp_path):
    from ai_skills.events import _EventLock
    from ai_skills.storage import LockTimeoutError
    events = tmp_path/'events'
    sentinel = tmp_path/'events.append.lock'
    sentinel.touch()  # An interrupted first creation may leave an empty file.
    ready = tmp_path/'ready.json'
    process = _event_lock_holder(events, ready)
    try:
        _wait_ready(process, ready)
        assert json.loads(ready.read_text()) == {'inheritable':False}
        before = sentinel.stat()
        started = time.monotonic()
        with pytest.raises(LockTimeoutError):
            with _EventLock(events, timeout_seconds=.06):
                raise AssertionError('live owner was not excluded')
        assert .05 <= time.monotonic()-started < 2
        process.kill()
        process.wait(timeout=5)
        bus = EventBus(events_log=events,status_file=tmp_path/'status')
        bus.status('STATUS|type=recovered-after-kill')
        assert events.read_text() == 'STATUS|type=recovered-after-kill\n'
        assert (tmp_path/'status').read_text() == events.read_text()
        after = sentinel.stat()
        assert (before.st_dev,before.st_ino) == (after.st_dev,after.st_ino)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


def test_separate_thread_handles_exclude_live_owner_and_release_after_exception(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ai_skills.events import _EventLock
    from ai_skills.storage import LockTimeoutError
    events = tmp_path/'events'
    def contend():
        with _EventLock(events,timeout_seconds=.05):
            return 'acquired'
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError,match='holder failed'):
            with _EventLock(events):
                future = executor.submit(contend)
                with pytest.raises(LockTimeoutError):
                    future.result(timeout=3)
                raise RuntimeError('holder failed')
        assert executor.submit(contend).result(timeout=3) == 'acquired'


def test_concurrent_thread_event_records_do_not_interleave(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    events = tmp_path/'events'
    def write(writer):
        bus = EventBus(events_log=events,status_file=tmp_path/'status')
        for index in range(10):
            bus.status(f'STATUS|type=fixture|writer={writer}|index={index}|payload='+'x'*12000)
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(write,range(6)))
    lines = events.read_text().splitlines()
    assert len(lines) == len(set(lines)) == 60
    assert all(line.count('STATUS|') == 1 and line.endswith('|payload='+'x'*12000) for line in lines)
    assert (tmp_path/'status').read_text().rstrip('\n') in lines


def test_event_lock_rejects_unexpected_kernel_error_without_busy_retry(tmp_path,monkeypatch):
    from ai_skills.events import _EventLock
    if os.name == 'nt':
        import msvcrt
        target,operation = msvcrt,'locking'
    else:
        import fcntl
        target,operation = fcntl,'flock'
    calls = []
    def broken(*args):
        calls.append(args)
        raise OSError(errno.EBADF,'unexpected kernel error')
    monkeypatch.setattr(target,operation,broken)
    lock = _EventLock(tmp_path/'events',timeout_seconds=1)
    with pytest.raises(OSError) as error:
        with lock:
            raise AssertionError('unexpected kernel error was ignored')
    assert error.value.errno == errno.EBADF
    assert len(calls) == 1
    assert lock.descriptor is None


@pytest.mark.parametrize('timeout',[True,-1,float('nan'),float('inf')])
def test_event_lock_rejects_invalid_timeout(tmp_path,timeout):
    from ai_skills.events import _EventLock
    with pytest.raises(ValueError,match='timeout_seconds'):
        _EventLock(tmp_path/'events',timeout_seconds=timeout)
