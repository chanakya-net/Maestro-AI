"""Real subprocess fixture; no providers or network access."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

def write_atomic(target: Path, content: str) -> None:
    tmp = target.with_name(f'{target.name}.{os.getpid()}.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, target)


mode, destination = sys.argv[1:3]
path = Path(destination)
if mode in ('tree', 'exit-leader', 'group-tree', 'group-exit-leader'):
    child = subprocess.Popen([sys.executable, __file__, 'branch-new-group' if mode.startswith('group-') else 'branch', destination])
    while not path.exists():
        time.sleep(.01)
    if mode.endswith('exit-leader'):
        sys.exit(0)
    child.wait()
elif mode in ('branch', 'branch-new-group'):
    if mode == 'branch-new-group':
        os.setpgid(0, 0)
    child = subprocess.Popen([sys.executable, __file__, 'leaf', destination + '.leaf'])
    write_atomic(path, json.dumps({'pid': os.getpid(), 'child': child.pid}))
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child.wait()
else:
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    write_atomic(path, str(os.getpid()))
    while True:
        time.sleep(.1)
