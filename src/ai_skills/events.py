"""Shared line-oriented status bus; records never contain injected delimiters."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import os
import tempfile
from contextlib import nullcontext

from ai_skills.storage import DirectoryLock



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
        with DirectoryLock(lock_path) if lock_path else nullcontext():
            if self.status_file:
                self.status_file.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(dir=self.status_file.parent)
                try:
                    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
                        stream.write(line + '\n')
                    os.replace(temporary, self.status_file)
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
