"""Review regressions for pool shutdown responsiveness and CLI failures."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from ai_skills import pool
from ai_skills.dispatch import stop_run
from ai_skills.storage import load_json, write_json_atomic


def test_default_polling_pool_acknowledges_stop_before_stop_deadline(tmp_path):
    state = tmp_path / '.run-with-it/main-state.json'
    write_json_atomic(state, {'schema_version': 4, 'issue_registry': {'1': {'status': 'pending', 'deps': [], 'context_file': str(tmp_path/'not-ready')}}, 'execution_plan': {'topo_order': [1]}})
    ready = tmp_path / 'sleeping'
    script = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ai_skills.pool import PoolConfig,run_pool
import ai_skills.pool as pool
original_sleep=time.sleep
def sleeping(seconds):
    Path(sys.argv[3]).touch()
    original_sleep(seconds)
pool.time.sleep=sleeping
raise SystemExit(run_pool(PoolConfig(state_file=Path(sys.argv[2]))).return_code)
'''
    source = Path(__file__).resolve().parents[2]/'src'
    process = subprocess.Popen([sys.executable, '-c', script, str(source), str(state), str(ready)],
                               env={**os.environ, 'RUN_WITH_IT_GITHUB_UPDATES': '0'},
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+15
        while not ready.exists():
            assert process.poll() is None, process.stderr.read().decode('utf-8', errors='replace')
            assert time.monotonic()<deadline, 'pool did not reach its polling wait'
            time.sleep(.01)
        assert stop_run(tmp_path, grace_seconds=.01).state == 'dead'
        assert process.wait(timeout=5) == 0
        assert load_json(tmp_path/'.run-with-it/main/pool.state.json')['state'] == 'stopped'
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        process.stderr.close()


def test_pool_cli_reports_detached_runtime_errors_without_traceback(tmp_path, monkeypatch, capsys):
    def failure(config): raise RuntimeError('owner handshake refused')
    monkeypatch.setattr(pool, 'run_pool', failure)
    assert pool.main(['--state-file', str(tmp_path/'state'), '--detach']) == 2
    captured = capsys.readouterr()
    assert 'runpool: owner handshake refused' in captured.err
    assert 'Traceback' not in captured.err


def test_pool_cli_reports_bad_record_without_traceback(tmp_path, capsys):
    config = tmp_path/'pool.json'; write_json_atomic(config, {'schema_version': 999})
    assert pool.main(['--config', str(config)]) == 2
    assert 'runpool:' in capsys.readouterr().err
