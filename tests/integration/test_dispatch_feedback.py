"""Regression coverage for verified PR feedback at dispatch boundaries."""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import psutil
import pytest

from ai_skills import dispatch
from ai_skills.dispatch import WorkerConfig, WatchConfig, dispatch_worker, watch_run
from ai_skills.storage import write_json_atomic
from test_dispatch import worker_config


@pytest.mark.parametrize(('value', 'expected'), [('0', False), ('false', False), ('no', False), ('off', False),
                                               ('1', True), ('true', True), ('yes', True), ('on', True)])
def test_detach_environment_boolean_controls_dispatch_mode(tmp_path, value, expected):
    config = worker_config(tmp_path, '')
    selected = WorkerConfig.from_sources({'agent_run': config.agent_run, 'state_file': config.state_file}, {'DETACH': value})
    assert selected.detach is expected


@pytest.mark.parametrize('value', ['maybe', 0, 1, None])
def test_direct_and_persisted_worker_configs_reject_non_boolean_detach(tmp_path, value):
    config = worker_config(tmp_path, '')
    with pytest.raises(ValueError, match='detach must be a boolean'):
        replace(config, detach=value)
    record = config.to_record()
    record['detach'] = value
    with pytest.raises(ValueError, match='detach must be a boolean'):
        WorkerConfig.from_record(record)


def test_detach_source_rejects_unrecognized_boolean(tmp_path):
    config = worker_config(tmp_path, '')
    with pytest.raises(ValueError, match='detach must be a boolean'):
        WorkerConfig.from_sources({'agent_run': config.agent_run, 'state_file': config.state_file}, {'DETACH': 'maybe'})


@pytest.mark.parametrize(('instructions', 'reason'), [('{broken', 'invalid-review-instructions-artifact'),
                                                     ('null', 'invalid-review-instructions-artifact'),
                                                     (None, 'missing-review-instructions-artifact')])
def test_inspection_reports_malformed_review_instructions_as_invalid(tmp_path, instructions, reason):
    config = worker_config(tmp_path, '')
    result_file = tmp_path / 'cycle-1-status.json'
    config = replace(config, agent_run=config.agent_run.replace(role='review', result_file=result_file))
    result_file.write_text('{"verdict":"approve","comment_count":0,"nitpick_only":false}')
    if instructions is not None:
        (tmp_path / 'cycle-1-instructions.json').write_text(instructions)
    assert dispatch.inspect_worker(config).artifact_reason == reason


def test_adoption_survives_head_disappearing_without_relaunch_or_cleanup(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], stderr=subprocess.DEVNULL, text=True).strip()
    git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'initial').write_text('initial')
    git('add', 'initial'); git('commit', '-m', 'initial')
    pre_head = git('rev-parse', 'HEAD')
    config = worker_config(tmp_path, "from pathlib import Path;Path('unverified-work').write_text('preserve me')")
    config = replace(config, agent_run=config.agent_run.replace(role='impl'), pre_spawn_head=pre_head)
    dispatch_worker(config)
    state = json.loads(config.state_file.read_text())
    state['state'] = 'running'  # Dispatcher lost before publishing its final state.
    write_json_atomic(config.state_file, state)
    git('update-ref', '-d', 'HEAD')
    result = dispatch.adopt_worker(config.state_file)
    assert result.state == 'failed'
    assert result.reason == 'missing-result-artifact'
    assert (tmp_path / 'unverified-work').read_text() == 'preserve me'
    assert not config.agent_run.result_file.exists()


def pool_lease(tmp_path, **changes):
    value = {'engine': 'python', 'state': 'running', 'pool_pid': os.getpid(),
             'pool_created_at': psutil.Process().create_time(), 'host': socket.gethostname()}
    value.update(changes)
    write_json_atomic(tmp_path / 'pool.json', value)
    return value


def test_new_pool_lease_does_not_replay_cached_empty_or_rewind_cursor(tmp_path):
    events = tmp_path / 'events'
    events.write_text('STATUS|type=pool-ready\nSTATUS|type=pool-empty\n')
    pool_lease(tmp_path, state='pool-empty', pool_created_at=psutil.Process().create_time() - 1)
    config = WatchConfig(events, tmp_path / 'pool.json', tmp_path / 'cursor', 0, .01)
    assert watch_run(config).result == 'pool-empty'
    consumed = json.loads(config.cursor_file.read_text())['offset']
    pool_lease(tmp_path)
    result = watch_run(config)
    assert result.result == 'running'
    assert result.lines == ()
    assert json.loads(config.cursor_file.read_text())['offset'] == consumed
    with events.open('a') as stream:
        stream.write('STATUS|type=pool-ready\nSTATUS|type=pool-empty\n')
    pool_lease(tmp_path, state='pool-empty')
    assert watch_run(config).result == 'pool-empty'
    assert watch_run(config).result == 'pool-empty'


def test_new_pool_lease_ignores_unread_empty_before_its_ready_marker(tmp_path):
    events = tmp_path / 'events'
    events.write_text('STATUS|type=pool-ready\n')
    pool_lease(tmp_path, pool_created_at=psutil.Process().create_time() - 1)
    config = WatchConfig(events, tmp_path / 'pool.json', tmp_path / 'cursor', 0, .01)
    assert watch_run(config).result == 'pool-dead'
    with events.open('a') as stream:
        stream.write('STATUS|type=pool-empty\n')
    pool_lease(tmp_path)
    result = watch_run(config)
    assert result.result == 'running'
    assert result.lines == ('STATUS|type=pool-empty',)
    with events.open('a') as stream:
        stream.write('STATUS|type=pool-ready\nSTATUS|type=heartbeat\n')
    result = watch_run(config)
    assert result.result == 'running'
    assert result.lines == ('STATUS|type=pool-ready', 'STATUS|type=heartbeat')


def test_unknown_pool_lease_does_not_reuse_cached_terminal(tmp_path):
    events = tmp_path / 'events'
    events.write_text('STATUS|type=pool-ready\nSTATUS|type=pool-empty\n')
    pool_lease(tmp_path, state='pool-empty')
    config = WatchConfig(events, tmp_path / 'pool.json', tmp_path / 'cursor', 0, .01)
    assert watch_run(config).result == 'pool-empty'
    write_json_atomic(config.pool_state_file, {})
    assert watch_run(config).result == 'pool-dead'


@pytest.mark.parametrize('lease', [{}, {'engine': 'python', 'state': 'pool-empty', 'host': 'fixture', 'pool_pid': 1, 'pool_created_at': 'unreadable'}])
def test_unreadable_pool_ownership_does_not_authorize_event_terminal(tmp_path, lease):
    events = tmp_path / 'events'
    events.write_text('STATUS|type=pool-empty\n')
    write_json_atomic(tmp_path / 'pool.json', lease)
    config = WatchConfig(events, tmp_path / 'pool.json', tmp_path / 'cursor', 0, .01)
    assert watch_run(config).result == 'pool-dead'


def test_provider_silence_states_ignore_wrapper_heartbeats_and_resume_on_output(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    config = worker_config(tmp_path, '''import os,time
from pathlib import Path
Path('provider-ready').touch()
while not Path('emit-output').exists(): time.sleep(.01)
print('provider activity', flush=True)
while not Path('release-provider').exists(): time.sleep(.01)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''', quiet_seconds=.08, stall_seconds=.2, hard_limit_seconds=5)
    def wait_for_state(expected):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = json.loads(config.state_file.read_text()) if config.state_file.exists() else {}
            if state.get('state') == expected:
                return state
            time.sleep(.01)
        pytest.fail(f'worker did not reach {expected}; latest state: {state}')
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(dispatch_worker, config)
        try:
            state = wait_for_state('stalled')
            assert state['stall_reason'] == 'alive-but-silent'
            assert not future.done()
            snapshot = dispatch.inspect_worker(config)
            assert snapshot.liveness == 'alive'
            events = config.agent_run.events_log.read_text()
            assert 'type=wrapper-heartbeat' in events
            assert events.count('type=worker-quiet|') == 1
            assert events.count('type=worker-stalled|') == 1
            (tmp_path / 'emit-output').touch()
            state = wait_for_state('running')
            assert state['stall_reason'] == ''
        finally:
            (tmp_path / 'emit-output').touch()
            (tmp_path / 'release-provider').touch()
        assert future.result(timeout=7).state == 'completed'


def test_detach_bootstrap_allows_capability_probe_before_provider_handshake(tmp_path):
    config = worker_config(tmp_path, '''import os,sys,time
from pathlib import Path
if '--help' in sys.argv:
    time.sleep(.3)
    print('--fixture-protocol')
else:
    while not Path('initialize-provider').exists(): time.sleep(.01)
    with Path('paid-attempts').open('a') as stream: stream.write('one\\n')
    while not Path('release-provider').exists(): time.sleep(.01)
    Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''', detach=True, bootstrap_seconds=.08)
    registry = json.loads(config.agent_run.registry_file.read_text())
    registry['agents']['fake']['invocation']['required_cli_flags'] = ['--fixture-protocol']
    config.agent_run.registry_file.write_text(json.dumps(registry))
    try:
        result = dispatch_worker(config)
        assert result.state in ('running', 'quiet', 'stalled'), result.reason
        assert result.identity is not None
        # Admission confirms the process identity, before its Python application
        # necessarily executes. Release initialization, then await its own ack.
        (tmp_path / 'initialize-provider').touch()
        deadline = time.monotonic() + 10
        while not (tmp_path / 'paid-attempts').is_file() or (tmp_path / 'paid-attempts').read_text().splitlines() != ['one']:
            assert time.monotonic() < deadline, 'provider did not acknowledge initialization'
            time.sleep(.01)
        assert (tmp_path / 'paid-attempts').read_text().splitlines() == ['one']
    finally:
        (tmp_path / 'initialize-provider').touch()
        (tmp_path / 'release-provider').touch()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = json.loads(config.state_file.read_text())
            if state['state'] in ('completed', 'failed'): break
            time.sleep(.02)
        dispatch._stop_provider(config)


def test_bootstrap_uncertain_shutdown_is_unknown_and_prohibits_relaunch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ai_skills.processes import Liveness, ProcessIdentity, StopResult
    config = worker_config(tmp_path, '', detach=True)
    class InaccessibleOwner:
        def __init__(self, directory):
            self.directory = directory
        def start(self, spec, *, run_id, attempt_id):
            return SimpleNamespace(identity=ProcessIdentity(987654321, 1, socket.gethostname(), run_id, attempt_id),
                                   close=lambda: StopResult('unknown', reason='owner inspection inaccessible'))
        def inspect(self, identity):
            # A detached owner disappeared before publishing provider admission.
            return Liveness('dead')
    monkeypatch.setattr(dispatch, 'ProcessSupervisor', InaccessibleOwner)
    result = dispatch_worker(config)
    assert result.state == 'unknown'
    assert result.return_code == 3
    assert json.loads(config.state_file.read_text())['state'] == 'unknown'
    with pytest.raises(RuntimeError, match='prohibits relaunch'):
        dispatch_worker(config)


def test_hard_limit_preserves_committed_progress_as_unverified_recovery(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], stderr=subprocess.DEVNULL, text=True).strip()
    git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'initial').write_text('initial')
    git('add', 'initial'); git('commit', '-m', 'initial')
    pre_head = git('rev-parse', 'HEAD')
    config = worker_config(tmp_path, '''import subprocess,time
from pathlib import Path
Path('committed-work').write_text('preserve unverified progress')
subprocess.check_call(['git','add','committed-work'])
subprocess.check_call(['git','commit','-m','implementation'])
time.sleep(20)
''', hard_limit_seconds=.5)
    config = replace(config, agent_run=config.agent_run.replace(role='impl'), pre_spawn_head=pre_head)
    result = dispatch_worker(config)
    assert result.state == 'artifact-recovery-required'
    assert result.return_code == 75
    assert dispatch.inspect_worker(config).liveness == 'dead'
    payload = json.loads(config.agent_run.result_file.read_text())
    assert payload['verification']['passed'] is False
    assert payload['status'] == 'artifact_recovery_required'


@pytest.mark.parametrize('wait_seconds', [0, .02])
def test_finished_pool_watch_drains_large_backlog_before_terminal_without_replay(tmp_path, wait_seconds):
    records = [f'STATUS|type=heartbeat|issue={number}|progress=λ' + 'x' * 960 for number in range(3300)]
    records += ['STATUS|type=dispatch-complete|issue=7', 'STATUS|type=pool-empty']
    encoded = ('\n'.join(records) + '\n').encode('utf-8')
    assert len(encoded) > 3 * 1024 * 1024
    events = tmp_path / 'events'
    events.write_bytes(encoded)
    pool_lease(tmp_path, state='pool-empty')
    config = WatchConfig(events, tmp_path / 'pool.json', tmp_path / 'cursor', wait_seconds, 5)
    delivered = []
    previous_offset = 0
    for _ in range(8):
        result = watch_run(config)
        delivered.extend(result.lines)
        offset = json.loads(config.cursor_file.read_text())['offset']
        assert 0 < offset - previous_offset <= 1024 * 1024
        previous_offset = offset
        assert result.return_code == 0
        if offset < len(encoded):
            assert result.result == 'running'
        else:
            assert result.result == 'pool-empty'
            break
    else:
        pytest.fail('bounded watch calls did not finish draining the terminal lease')
    assert delivered == records
    assert watch_run(config).lines == ()
    assert watch_run(config).result == 'pool-empty'
