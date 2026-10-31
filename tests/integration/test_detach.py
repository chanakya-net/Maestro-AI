import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from test_processes import FIXTURE, alive, api, wait_for


@pytest.mark.parametrize('crash', [False, True])
def test_invoking_process_exit_leaves_independent_adoptable_attempts(tmp_path, crash):
    _, Supervisor = api()
    code = '''from ai_skills.processes import ProcessSpec, ProcessSupervisor
from pathlib import Path
import sys
s=ProcessSupervisor(Path(sys.argv[1]))
for name in ('one','two'):
 s.start(ProcessSpec((sys.executable,sys.argv[2],'leaf',str(Path(sys.argv[1])/name)),Path(sys.argv[1]),{}),run_id='run',attempt_id=name)
'''
    argv = [sys.executable, '-c', code + ('\nimport time;time.sleep(60)' if crash else ''), str(tmp_path), str(FIXTURE)]
    if crash:
        launcher = subprocess.Popen(argv)
        try:
            wait_for(lambda: (tmp_path / 'one').exists() and (tmp_path / 'two').exists())
        finally:
            launcher.kill()
            launcher.wait(timeout=5)
    else:
        subprocess.run(argv, check=True)
    from ai_skills.processes import ProcessIdentity
    supervisor = Supervisor(tmp_path)
    records = supervisor.attempt_records()
    identities = [ProcessIdentity(**r['identity']) for r in records]
    assert len(identities) == 2
    try:
        assert all(supervisor.inspect(i).state == 'alive' for i in identities)
        assert supervisor.stop(identities[0], grace_seconds=.1).state == 'dead'
        assert supervisor.inspect(identities[1]).state == 'alive'
    finally:
        for identity in identities:
            supervisor.stop(identity, grace_seconds=.1)


@pytest.mark.parametrize('mode', ['tree', 'exit-leader'])
def test_owner_death_has_explicit_recovery(tmp_path, mode):
    Spec, Supervisor = api()
    supervisor = Supervisor(tmp_path / 'records')
    marker = tmp_path / 'tree'
    owned = supervisor.start(Spec((sys.executable, str(FIXTURE), mode, str(marker)), tmp_path, {}), run_id='run', attempt_id='one')
    try:
        wait_for(marker.exists)
        members = json.loads(marker.read_text())
        if mode == 'exit-leader':
            wait_for(lambda: not alive(owned.identity.pid))
        wait_for(lambda: len(supervisor.lookup_attempt('run', 'one').get('members', [])) >= (2 if mode == 'exit-leader' else 3))
        owner_pid = supervisor.lookup_attempt('run', 'one')['owner']['pid']
        import psutil
        psutil.Process(owner_pid).kill()
        wait_for(lambda: not alive(owner_pid))
        if os.name == 'nt':
            wait_for(lambda: not alive(members['child']))
            assert supervisor.inspect(owned.identity).state == 'dead'
        else:
            assert supervisor.inspect(owned.identity).state == 'alive'
            assert supervisor.stop(owned.identity, grace_seconds=.1).state == 'dead'
        wait_for(lambda: not alive(members['pid']))
        wait_for(lambda: not alive(members['child']))
    finally:
        owned.close()
