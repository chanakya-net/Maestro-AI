from ai_skills.pair import ENGINE_VERSION


def test_native_engine_version_is_distinct_from_legacy():
    assert ENGINE_VERSION == 'ai-skills-pair/1'

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import pytest
import psutil

from ai_skills.pair import PairConfig, PairRun
from ai_skills.storage import write_json_atomic
from test_pair import pair_fixture, invoke, decision_file


def background(run):
    source = 'import sys;from ai_skills.pair import main;sys.exit(main(sys.argv[1:]))'
    return subprocess.Popen([sys.executable, '-c', source, 'run-round', '--run-dir', str(run)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def await_started(root, process=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (root / 'one-r1.count').exists() and (root / 'two-r1.count').exists(): return
        if process and process.poll() is not None: pytest.fail('coordinator exited before providers started')
        time.sleep(.03)
    pytest.fail('providers never started')


def await_owned_attempts_completed(run):
    from ai_skills.processes import ProcessIdentity, ProcessSupervisor
    deadline = time.monotonic() + 15
    observed = {}
    while time.monotonic() < deadline:
        for kind in ('workers', 'processes'):
            supervisor = ProcessSupervisor(run / kind)
            records = supervisor.attempt_records()
            observed[kind] = []
            for record in records:
                state = supervisor.inspect(ProcessIdentity(**record['identity'])).state if record.get('identity') else 'starting'
                if state == 'dead' and (record.get('state') != 'dead' or type(record.get('return_code')) is not int):
                    state = 'awaiting-durable-exit'
                observed[kind].append(state)
        if all(len(states) == 2 and all(state == 'dead' for state in states) for states in observed.values()):
            return
        time.sleep(.03)
    pytest.fail('owned pair attempts did not complete: ' + repr(observed))


def test_interrupted_coordinator_collects_live_attempts_without_duplicate_paid_work(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    monkeypatch.setenv('FAKE_MODE', 'gated')
    run = start()
    process = background(run)
    try:
        await_started(root, process)
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
        process.kill(); process.wait(timeout=5)
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
        (root / 'providers.release').touch()
        await_owned_attempts_completed(run)
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
        assert (root / 'one-r1.count').read_text() == '1'
        assert (root / 'two-r1.count').read_text() == '1'
    finally:
        (root / 'providers.release').touch()
        if process.poll() is None: process.kill(); process.wait()
        invoke(capsys, 'reap', '--run-dir', str(run), '--force')


def test_foreign_lock_explicit_break_and_unknown_attempt_refusal(pair_fixture, capsys):
    start, root, _ = pair_fixture
    run = start(); lock = run / '.command.lock'; lock.mkdir()
    write_json_atomic(lock / 'owner.json', {'host': 'foreign-machine', 'pid': 1, 'create_time': 1, 'token': 'foreign'})
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
    assert invoke(capsys, 'run-round', '--run-dir', str(run), '--break-lock')[0] == 0
    run = start(); state = json.loads((run / 'state.json').read_text())
    state['rounds']['1']['agents']['1'].update(status='running', attempts=1)
    write_json_atomic(run / 'state.json', state)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
    assert invoke(capsys, 'reap', '--run-dir', str(run), '--force')[0] == 4
    assert not (run / 'rounds/round-01/agent-1-attempt-02-spec.json').exists()


def test_same_host_dead_lock_reclaims_and_live_lock_cannot_break(pair_fixture, capsys):
    start, _, _ = pair_fixture
    run = start(); lock = run / '.command.lock'; lock.mkdir()
    write_json_atomic(lock / 'owner.json', {'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time() - 100, 'token': 'stale'})
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    run = start(); lock = run / '.command.lock'; lock.mkdir()
    write_json_atomic(lock / 'owner.json', {'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time(), 'token': 'live'})
    assert invoke(capsys, 'run-round', '--run-dir', str(run), '--break-lock')[0] == 4
    assert lock.exists()


def test_workspace_binding_and_foreign_or_incompatible_engines(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    run = start(); other = root / 'other'; other.mkdir(); monkeypatch.chdir(other); monkeypatch.setenv('REPO_ROOT', str(other))
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert (root / 'one-cwd.txt').read_text() == str(root)
    assert invoke(capsys, 'run-round', '--run-dir', str(run), '--bind-workspace', str(other))[0] == 0 # already complete round, no launch
    run = start(); state = json.loads((run / 'state.json').read_text()); state['host'] = 'different-host'; write_json_atomic(run / 'state.json', state)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
    state['host'] = socket.gethostname(); state['engine_version'] = 'future/99'; write_json_atomic(run / 'state.json', state)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 2


def test_legacy_import_backs_up_and_requires_quiescence_and_binding(pair_fixture, capsys):
    start, root, _ = pair_fixture
    run = start(); state = json.loads((run / 'state.json').read_text())
    state.pop('engine_version'); state.pop('workspace_root'); state['schema_version'] = 2; write_json_atomic(run / 'state.json', state)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 2
    assert (run / 'legacy-backup/state.json').exists()
    assert invoke(capsys, 'run-round', '--run-dir', str(run), '--bind-workspace', str(root))[0] == 0
    run = start(); state = json.loads((run / 'state.json').read_text()); state.pop('engine_version'); state['schema_version'] = 2
    state['rounds']['1']['agents']['1'].update(status='running', attempts=1); write_json_atomic(run / 'state.json', state)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
    assert not (run / 'legacy-backup').exists()


def test_interrupted_decision_transition_and_frozen_input_conflict(pair_fixture, capsys):
    start, root, _ = pair_fixture
    run = start(); assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    decision = decision_file(root, 'CONTINUE')
    target = run / 'rounds/round-01/coordinator-decision.md'; target.write_bytes(decision.read_bytes())
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision))[0] == 0
    assert (run / 'rounds/round-02/snapshot.md').is_file()
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision))[0] == 0
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    target = run / 'rounds/round-02/coordinator-decision.md'; target.write_text('different')
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision))[0] == 2


def test_force_reap_stops_native_process_tree(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    monkeypatch.setenv('FAKE_MODE', 'hang')
    run = start(); process = background(run)
    try:
        await_started(root, process)
        deadline = time.monotonic() + 5
        while not (root / 'grandchild.pid').exists() and time.monotonic() < deadline: time.sleep(.03)
        child = int((root / 'grandchild.pid').read_text())
        process.kill(); process.wait(timeout=5)
        assert invoke(capsys, 'reap', '--run-dir', str(run))[0] == 4
        assert invoke(capsys, 'reap', '--run-dir', str(run), '--force')[0] == 0
        assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE
    finally:
        if process.poll() is None: process.kill(); process.wait()
        invoke(capsys, 'reap', '--run-dir', str(run), '--force')


@pytest.mark.parametrize('transport', ['argv', 'agy-stream-json'])
def test_killed_agent_worker_recovers_provider_output_without_another_launch(pair_fixture, monkeypatch, capsys, transport):
    start, root, registry_path = pair_fixture
    if transport == 'agy-stream-json':
        registry = json.loads(registry_path.read_text())
        invocation = registry['agents']['fake']['invocation']
        invocation['prompt_transport'] = transport
        invocation['args_template'].remove('{{prompt}}')
        invocation['args_template'].extend(['--input-format', 'stream-json', '--output-format', 'stream-json'])
        registry_path.write_text(json.dumps(registry))
        script = root/'provider.py'
        source = script.read_text().replace('payload=sys.argv[-1]', "payload=json.loads(sys.stdin.buffer.read())['message']['content']")
        source = source.replace('sections=', 'import io\nsys.stdout=io.StringIO()\nsections=')
        source += "\nresponse=sys.stdout.getvalue();sys.stdout=sys.__stdout__;print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':response}}))\n"
        script.write_text(source, encoding='utf-8')
    monkeypatch.setenv('FAKE_MODE', 'gated')
    run = start(); process = background(run)
    try:
        await_started(root, process)
        process.kill(); process.wait(timeout=5)
        worker_records = [json.loads(p.read_text()) for p in (run / 'workers').glob('*.json')]
        # Kill the actual wrapper, leaving its independently owned provider
        # alive. Its raw stdout and durable exit must still be collected.
        psutil.Process(worker_records[0]['identity']['pid']).kill()
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
        (root / 'providers.release').touch()
        await_owned_attempts_completed(run)
        code, _, err = invoke(capsys, 'run-round', '--run-dir', str(run)); assert code == 0, err
        assert (root / 'one-r1.count').read_text() == '1'
        assert (root / 'two-r1.count').read_text() == '1'
        canonical = list((run / 'rounds/round-01').glob('agent-*-run.json'))
        assert any(json.loads(p.read_text()).get('recovered') for p in canonical)
    finally:
        (root / 'providers.release').touch()
        if process.poll() is None: process.kill(); process.wait()
        invoke(capsys, 'reap', '--run-dir', str(run), '--force')


def test_timeout_stops_entire_owned_provider_tree_and_preserves_success(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    monkeypatch.setenv('FAKE_MODE', 'hang'); monkeypatch.setenv('PAIR_COLLEAGUE_TERMINATION_GRACE_SECONDS', '0')
    run = start('--timeout-seconds', '1')
    code, _, err = invoke(capsys, 'run-round', '--run-dir', str(run)); assert code == 1, err
    child = int((root / 'grandchild.pid').read_text())
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE
    assert (run / 'rounds/round-01/agent-1-output.md').is_file()
    record = json.loads((run / 'rounds/round-01/agent-2-attempt-01-run.json').read_text())
    assert record['timed_out'] and record['exit_code'] != 0


def test_windows_path_and_foreign_attempt_refused_before_launch(pair_fixture, capsys):
    start, _, _ = pair_fixture
    run = start(); state = json.loads((run / 'state.json').read_text())
    if os.name != 'nt':
        state['workspace_root'] = 'C:\\reviewed\\workspace'; write_json_atomic(run / 'state.json', state)
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 2
    run = start(); state = json.loads((run / 'state.json').read_text())
    state['rounds']['1']['agents']['1'].update(status='running', attempts=1); write_json_atomic(run / 'state.json', state)
    manifest = {'host': 'foreign-provider-host', 'snapshot_sha256': state['rounds']['1']['snapshot_sha256'], 'workspace_root': state['workspace_root']}
    write_json_atomic(run / 'rounds/round-01/agent-1-attempt-01-run.json', manifest)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 4
    assert invoke(capsys, 'reap', '--run-dir', str(run), '--force')[0] == 4


def test_real_legacy_config_is_pinned_before_launch_and_keeps_original_backup(pair_fixture, monkeypatch):
    from ai_skills.installation import resolve_runtime
    start, root, _ = pair_fixture
    run = start()
    state = json.loads((run / 'state.json').read_text())
    state.pop('engine_version'); state['schema_version'] = 2
    config = json.loads((run / 'config.json').read_text())
    config.pop('engine_version'); config.pop('engine_runtime'); config['schema_version'] = 2
    write_json_atomic(run / 'state.json', state)
    write_json_atomic(run / 'config.json', config)
    original = (run / 'config.json').read_bytes()
    # A lexical interpreter path can be a venv symlink; preserve that path.
    importing_python = root / 'import-runtime' / 'python'
    importing_python.parent.mkdir()
    if os.name != 'nt':
        target = root / 'interpreter-target'; target.touch()
        importing_python.symlink_to(target)
    else:
        importing_python.touch()
    monkeypatch.setattr(sys, 'executable', str(importing_python))
    pair = PairRun(PairConfig(run_dir=run))
    pair.load(); pair.import_legacy()
    imported = json.loads((run / 'config.json').read_text())
    assert imported['engine_runtime']['python'] == str(importing_python)
    assert (run / 'legacy-backup/config.json').read_bytes() == original
    assert not list(root.glob('*-r1.count'))
    active = root / 'assets'; active.mkdir()
    replacement = root / 'new-python'; replacement.touch()
    write_json_atomic(active / 'runtime.json', {'schema_version': 1, 'python': str(replacement)})
    assert resolve_runtime(run, assets_dest=active) == importing_python
    importing_python.unlink()
    with pytest.raises(ValueError, match='pinned.*missing'):
        resolve_runtime(run, assets_dest=active)
