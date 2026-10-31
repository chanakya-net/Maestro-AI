"""Durable, independently owned process attempts.

Each start launches a detached owner, not a provider directly. Callers may exit
without closing the attempt; explicit close/context exit stops it. Restarting
callers adopt by identity using the same record directory. Attempt IDs cannot be
reused, even after completion; a new attempt must have a new ID.

Records are trusted local state, not an authorization boundary against another
process running as the same user. Unknown identity never permits killing or
relaunching. POSIX owner SIGKILL leaves the session for anchored reconciliation;
Windows owner death closes its Job Object and kills its members.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import psutil

from ai_skills.storage import write_json_atomic


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: float
    host: str
    run_id: str
    attempt_id: str


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    stdout: Path | None = None
    stderr: Path | None = None


@dataclass(frozen=True)
class Liveness:
    state: str
    members: tuple[int, ...] = ()
    reason: str = ''


@dataclass(frozen=True)
class StopResult:
    state: str
    remaining: tuple[int, ...] = ()
    reason: str = ''


def _read(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def _match(member):
    try:
        process = psutil.Process(member['pid'])
        if process.create_time() != member['created_at']:
            return 'mismatch'
        return 'dead' if process.status() == psutil.STATUS_ZOMBIE else 'alive'
    except psutil.NoSuchProcess:
        return 'dead'
    except (psutil.AccessDenied, PermissionError):
        return 'unknown'


class OwnedProcess:
    def __init__(self, supervisor, identity, record_path):
        self.supervisor = supervisor
        self.identity = identity
        self.record_path = record_path

    def close(self, *, grace_seconds=1.0):
        return self.supervisor.stop(self.identity, grace_seconds=grace_seconds)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ProcessSupervisor:
    def __init__(self, record_directory: Path):
        self.directory = Path(record_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._owners = {}

    def _reap_owners(self):
        for path, owner in list(self._owners.items()):
            if owner.poll() is not None:
                del self._owners[path]

    def _dead(self, identity):
        path = self._path(identity.run_id, identity.attempt_id)
        owner = self._owners.get(path)
        if owner is not None:
            try:
                owner.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self._reap_owners()
        return Liveness('dead')

    def _path(self, run_id, attempt_id):
        key = hashlib.sha256(json.dumps([run_id, attempt_id]).encode()).hexdigest()
        return self.directory / (key + '.json')

    def start(self, spec: ProcessSpec, *, run_id: str, attempt_id: str) -> OwnedProcess:
        self._reap_owners()
        if not run_id or not attempt_id or not spec.argv:
            raise ValueError('run_id, attempt_id and argv are required')
        path = self._path(run_id, attempt_id)
        record = {'schema_version': 1, 'state': 'starting', 'host': socket.gethostname(),
                  'run_id': run_id, 'attempt_id': attempt_id,
                  'spec': {'argv': list(spec.argv), 'cwd': str(Path(spec.cwd).resolve()),
                           'stdout': str(Path(spec.stdout).resolve()) if spec.stdout else None,
                           'stderr': str(Path(spec.stderr).resolve()) if spec.stderr else None}}
        # Exclusive creation is the per-attempt admission lock. Even corrupt or
        # unfinished records block another launch; a timeout is not permission.
        try:
            with path.open('x', encoding='utf-8') as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise RuntimeError('existing attempt record prohibits another launch') from None
        env = os.environ.copy()
        env.update(spec.env)
        # Preserve the installed pinned interpreter; source-tree invocations also
        # need an absolute import root because an owner has a different cwd.
        root = str(Path(__file__).resolve().parents[1])
        options = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            bootstrap = 'import sys;sys.path.insert(0,sys.argv[1]);from ai_skills.processes import _own;from pathlib import Path;raise SystemExit(_own(Path(sys.argv[2])))'
            owner = subprocess.Popen([sys.executable, '-c', bootstrap, root, str(path)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=env, **options)
        except OSError as error:
            record.update(state='failed', error=str(error))
            write_json_atomic(path, record)
            raise RuntimeError(f'owner launch failed: {error}') from error
        self._owners[path] = owner
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = _read(path)
            if record['state'] == 'failed':
                owner.wait(timeout=3)
                raise RuntimeError('process launch failed: ' + record.get('error', 'unknown error'))
            if 'identity' in record:
                return OwnedProcess(self, ProcessIdentity(**record['identity']), path)
            if owner.poll() is not None:
                raise RuntimeError('owner launch failed before durable handshake; ownership unknown')
            time.sleep(.03)
        raise RuntimeError('owner launch handshake timed out; ownership unknown')

    def _record(self, identity):
        if identity.host != socket.gethostname():
            raise ValueError('foreign host')
        record = _read(self._path(identity.run_id, identity.attempt_id))
        if record.get('schema_version') != 1 or record.get('identity') != asdict(identity):
            raise ValueError('unrecognized or mismatched identity')
        return record

    def inspect(self, identity: ProcessIdentity) -> Liveness:
        self._reap_owners()
        try:
            record = self._record(identity)
            owner_state = _match(record['owner'])
            if owner_state == 'unknown':
                return Liveness('unknown', reason='owner identity inaccessible')
            if os.name != 'nt':
                from ai_skills.platforms.posix import members
                current = members(identity.pid)
                if not current:
                    return self._dead(identity)
                anchors = [record['identity'], *record.get('members', [])]
                if not any(_match(a) == 'alive' and a in current for a in
                           ({'pid': x['pid'], 'created_at': x['created_at']} for x in anchors)):
                    return Liveness('unknown', reason='session has no verified creation-time anchor')
                return Liveness('alive', tuple(m['pid'] for m in current))
            if record['state'] == 'dead':
                return self._dead(identity)
            if owner_state == 'alive':
                if time.time() - record.get('updated_at', 0) > 5:
                    return Liveness('unknown', reason='owner heartbeat stale')
                return Liveness('alive', tuple(m['pid'] for m in record.get('members', [])))
            # A confirmed dead Windows owner drops the only job handle. Give the
            # kernel time to finish cleanup, retaining uncertainty while pending.
            statuses = [_match(m) for m in record.get('members', [])]
            if all(s == 'dead' for s in statuses):
                return self._dead(identity)
            return Liveness('unknown', reason='owner gone; job cleanup not yet verified')
        except (OSError, ValueError, KeyError, TypeError, psutil.Error) as error:
            return Liveness('unknown', reason=str(error))

    def stop(self, identity: ProcessIdentity, *, grace_seconds: float = 1.0) -> StopResult:
        if not math.isfinite(grace_seconds) or not 0 <= grace_seconds <= 300:
            raise ValueError('grace_seconds must be between 0 and 300')
        status = self.inspect(identity)
        if status.state != 'alive':
            return StopResult(status.state, status.members, status.reason)
        path = self._path(identity.run_id, identity.attempt_id)
        record = self._record(identity)
        if _match(record['owner']) == 'alive':
            write_json_atomic(path.with_suffix('.stop'), {'identity': asdict(identity), 'grace_seconds': grace_seconds})
            deadline = time.monotonic() + grace_seconds + 5
            while time.monotonic() < deadline:
                status = self.inspect(identity)
                if status.state == 'dead':
                    owner = self._owners.get(path)
                    if owner is not None:
                        try:
                            owner.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
                    return StopResult('dead')
                time.sleep(.03)
            return StopResult('unknown', status.members, 'owner did not confirm shutdown')
        if os.name != 'nt':
            from ai_skills.platforms.posix import terminate
            # Recheck immediately before signaling the session after owner loss.
            status = self.inspect(identity)
            if status.state != 'alive':
                return StopResult(status.state, status.members, status.reason)
            try:
                remaining = terminate(identity.pid, grace_seconds, [record['identity'], *record.get('members', [])])
                return StopResult('alive' if remaining else 'dead', tuple(m['pid'] for m in remaining))
            except (OSError, psutil.Error) as error:
                return StopResult('unknown', reason=str(error))
        return StopResult('unknown', status.members, 'Windows owner is unavailable')


def _own(path):
    if os.name == 'nt':
        from ai_skills.platforms import windows as platform
    else:
        from ai_skills.platforms import posix as platform
    record = _read(path)
    group = None
    stopping = False

    def interrupted(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with ExitStack() as stack:
            spec = record.pop('spec')
            stdin = stack.enter_context(open(os.devnull, 'rb'))
            stdout = stack.enter_context(open(spec['stdout'] or os.devnull, 'ab'))
            stderr = stack.enter_context(open(spec['stderr'] or os.devnull, 'ab'))
            environment = os.environ.copy()
            group = platform.ProcessGroup(spec['argv'], spec['cwd'], environment, stdin, stdout, stderr)
            record.update(identity=asdict(ProcessIdentity(group.pid, group.created_at, record['host'],
                                                         record['run_id'], record['attempt_id'])),
                          owner={'pid': os.getpid(), 'created_at': psutil.Process().create_time()},
                          state='alive')
            while True:
                current = group.members()
                record.update(members=current, updated_at=time.time(), state='alive' if current else 'dead')
                write_json_atomic(path, record)
                if not current:
                    break
                request = path.with_suffix('.stop')
                grace = 1.0
                if request.exists():
                    command = _read(request)
                    if command.get('identity') == record['identity']:
                        grace = float(command['grace_seconds'])
                        stopping = True
                if stopping:
                    remaining = group.stop(grace)
                    record.update(members=remaining, updated_at=time.time(), state='alive' if remaining else 'dead')
                    write_json_atomic(path, record)
                    break
                time.sleep(.05)
    except BaseException as error:
        if group is not None:
            try:
                group.stop(0)
            except BaseException:
                pass
        record.update(state='failed', error=str(error), updated_at=time.time())
        write_json_atomic(path, record)
        return 1
    finally:
        if group is not None:
            group.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(_own(Path(sys.argv[1])))
