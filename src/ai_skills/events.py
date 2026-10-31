"""Shared line-oriented status bus; records never contain injected delimiters."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import errno
import math
import stat
import time
import os
import tempfile
from contextlib import nullcontext

from ai_skills.storage import LockTimeoutError, replace_file_atomic



class _EventLock:
    """Event-only kernel lock; closing/killing the holder always releases it.

    The regular sentinel persists and is never unlinked/replaced, so all
    processes and separate thread handles contend on the same file identity.
    Authoritative state/lease locks deliberately retain DirectoryLock semantics.
    """

    def __init__(self, path, *, timeout_seconds=10.0):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError('timeout_seconds must be finite and nonnegative')
        self.path = Path(str(path) + '.append.lock')
        self.timeout_seconds = timeout_seconds
        self.descriptor = None

    def _try_lock(self):
        if os.name == 'nt':
            import msvcrt
            # Locking beyond EOF is supported; seed an initially empty sentinel
            # only after acquisition so another holder cannot block that write.
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __enter__(self):
        if self.descriptor is not None:
            raise RuntimeError('event lock instance is already entered')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0), 0o600)
        self.descriptor = descriptor
        try:
            os.set_inheritable(descriptor, False)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('event lock sentinel must be a regular file')
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    self._try_lock()
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeoutError(f'timed out waiting for live event writer: {self.path}') from error
                    time.sleep(min(.01, remaining))
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b'\0')
            return self
        except BaseException:
            os.close(descriptor)
            self.descriptor = None
            raise

    def __exit__(self, *_):
        descriptor, self.descriptor = self.descriptor, None
        if descriptor is not None:
            os.close(descriptor)


def normalize(value: object) -> str:
    return str(value).replace('\r', ' ').replace('\n', ' ').replace('|', '/') if value is not None and str(value) else 'unknown'


def emit_status(kind: str, fields: Mapping[str, object]) -> str:
    return 'STATUS|type=' + normalize(kind) + ''.join(f'|{normalize(key)}={normalize(value)}' for key, value in fields.items())


class EventBus:
    def __init__(self, *, status_file=None, events_log=None, log_file=None):
        self.status_file = Path(status_file) if status_file else None
        self.events_log = Path(events_log) if events_log else None
        self.log_file = Path(log_file) if log_file else None

    @staticmethod
    def append(path, text):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8', newline='') as stream:
                stream.write(text)

    def status(self, line):
        lock_path = self.events_log or self.status_file
        with _EventLock(lock_path) if lock_path else nullcontext():
            if self.status_file:
                self.status_file.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(dir=self.status_file.parent)
                try:
                    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
                        stream.write(line + '\n')
                    replace_file_atomic(Path(temporary), self.status_file)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            self.append(self.events_log, line + '\n')

    def forward(self, line, channel='stderr', *, newline=True, record_status=True, suppress_console=False):
        text = line + ('\n' if newline else '')
        self.append(self.log_file, line + '\n')
        if record_status and line.startswith(('STATUS|', 'ROUTE|', 'COMPLEXITY|')):
            self.status(line)
        if not suppress_console and not line.startswith('STATUS|type=heartbeat|'):
            target = sys.stdout if channel == 'stdout' else sys.stderr
            target.write(text)
            target.flush()

    def emit(self, kind, fields):
        self.forward(emit_status(kind, fields))
