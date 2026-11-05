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


def test_nested_worker_admission_does_not_reuse_managed_parent_identity(tmp_path):
    from ai_skills.processes import ProcessSupervisor
    config = worker_config(tmp_path, "import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    records = tmp_path / 'records'
    supervisor = ProcessSupervisor(records)
    claim = supervisor._path('parent-run', 'parent-attempt')
    claim.write_text(json.dumps({'schema_version': 1, 'run_id': 'parent-run',
                                'attempt_id': 'parent-attempt', 'state': 'starting'}))
    original_claim = claim.read_bytes()
    env = {**os.environ, 'RUN_WITH_IT_DISPATCHER_MANAGED': '1',
           'RUN_WITH_IT_RUN_ID': 'parent-run', 'RUN_WITH_IT_ATTEMPT_ID': 'parent-attempt'}
    child = WorkerConfig.from_sources({
        'agent': 'fake', 'role': 'fixture', 'issue': '7', 'gui_mode': '0',
        'repo_root': tmp_path, 'registry_file': config.agent_run.registry_file,
        'context_file': config.agent_run.context_file, 'prompt_file': config.agent_run.prompt_file,
        'state_file': config.state_file, 'result_file': config.agent_run.result_file,
        'log_file': config.agent_run.log_file, 'record_directory': records, 'poll_seconds': .02,
    }, env)
    assert dispatch_worker(child).state == 'completed'
    saved = json.loads(config.state_file.read_text())
    assert saved['run_id'] != 'parent-run'
    assert saved['attempt_id'] != 'parent-attempt'
    assert claim.read_bytes() == original_claim


@pytest.mark.parametrize('nested', [False, True])
def test_managed_child_keeps_explicit_run_and_attempt_identity(tmp_path, nested):
    values = {'run_id': 'explicit-child-run', 'attempt_id': 'explicit-child-attempt'}
    source = {'agent_run': values, 'state_file': tmp_path / 'child.json'} if nested else {**values, 'state_file': tmp_path / 'child.json'}
    selected = WorkerConfig.from_sources(source, {'RUN_WITH_IT_DISPATCHER_MANAGED': '1',
        'RUN_WITH_IT_RUN_ID': 'parent-run', 'RUN_WITH_IT_ATTEMPT_ID': 'parent-attempt'})
    assert (selected.agent_run.run_id, selected.agent_run.attempt_id) == ('explicit-child-run', 'explicit-child-attempt')


@pytest.mark.parametrize('managed', [None, '0'])
def test_unmanaged_worker_can_still_take_identity_from_explicit_environment(tmp_path, managed):
    env = {'RUN_WITH_IT_RUN_ID': 'configured-run', 'RUN_WITH_IT_ATTEMPT_ID': 'configured-attempt'}
    if managed is not None:
        env['RUN_WITH_IT_DISPATCHER_MANAGED'] = managed
    selected = WorkerConfig.from_sources({'state_file': tmp_path / 'worker.json'}, env)
    assert (selected.agent_run.run_id, selected.agent_run.attempt_id) == ('configured-run', 'configured-attempt')


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


def test_provider_silence_states_ignore_wrapper_heartbeats_and_resume_on_output(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    # Advance only the dispatch observer's silence clock. Runner heartbeats,
    # owned process admission, provider streams and polling still use real time.
    observed_time = [0.0]
    monkeypatch.setattr(dispatch, 'time', SimpleNamespace(
        monotonic=lambda: observed_time[0], time=time.time, sleep=time.sleep))
    config = worker_config(tmp_path, '''import os,time
from pathlib import Path
Path('provider-ready').touch()
while not Path('emit-output').exists(): time.sleep(.01)
print('provider activity', flush=True)
while not Path('release-provider').exists(): time.sleep(.01)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''', quiet_seconds=.08, stall_seconds=.2, hard_limit_seconds=5)
    def wait_for_state(expected, event=None, minimum_heartbeats=0):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = json.loads(config.state_file.read_text()) if config.state_file.exists() else {}
            text = config.agent_run.events_log.read_text() if config.agent_run.events_log.exists() else ''
            events = ''.join(line for line in text.splitlines(keepends=True) if line.endswith('\n'))
            if (state.get('state') == expected
                    and (event is None or f'type={event}|' in events)
                    and events.count('type=wrapper-heartbeat|') >= minimum_heartbeats):
                return state, events
            time.sleep(.01)
        pytest.fail(f'worker did not reach {expected}/{event}; latest state: {state}; events: {text}')
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(dispatch_worker, config)
        try:
            _, events = wait_for_state('running', minimum_heartbeats=1)
            observed_time[0] = .1
            state, events = wait_for_state('quiet', 'worker-quiet',
                minimum_heartbeats=events.count('type=wrapper-heartbeat|') + 1)
            assert state['stall_reason'] == 'alive-but-quiet'
            observed_time[0] = .3
            state, events = wait_for_state('stalled', 'worker-stalled',
                minimum_heartbeats=events.count('type=wrapper-heartbeat|') + 1)
            assert state['stall_reason'] == 'alive-but-silent'
            assert not future.done()
            snapshot = dispatch.inspect_worker(config)
            assert snapshot.liveness == 'alive'
            assert 'type=wrapper-heartbeat' in events
            assert events.count('type=worker-quiet|') == 1
            assert events.count('type=worker-stalled|') == 1
            (tmp_path / 'emit-output').touch()
            state, _ = wait_for_state('running')
            assert state['stall_reason'] == ''
        finally:
            (tmp_path / 'emit-output').touch()
            (tmp_path / 'release-provider').touch()
        assert future.result(timeout=7).state == 'completed'
    events = config.agent_run.events_log.read_text()
    assert events.count('type=worker-quiet|') == 1
    assert events.count('type=worker-stalled|') == 1


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


def test_detach_long_poll_publishes_provider_before_bootstrap_expires(tmp_path, monkeypatch):
    from ai_skills.processes import ProcessIdentity, ProcessSupervisor
    config = replace(worker_config(tmp_path, '''import os,time
from pathlib import Path
with Path('paid-attempts').open('a') as stream: stream.write('one\\n')
while not Path('release-provider').exists(): time.sleep(.01)
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
''', detach=True, bootstrap_seconds=.08), poll_seconds=20)
    real_start = ProcessSupervisor.start
    def start_after_initial_observation(supervisor, spec, *, run_id, attempt_id):
        if run_id.endswith('-dispatcher'):
            # Coordinate the real detached runner with its first empty read.
            # This reproduces the publication race without scheduler timing.
            bootstrap = '''import sys,threading
sys.path.insert(0,sys.argv[1])
from ai_skills import dispatch
initial_observation = threading.Event()
real_record, real_run = dispatch._process_record, dispatch.run_agent
def record(config):
    value = real_record(config)
    if not value.get('identity'): initial_observation.set()
    return value
def run(config):
    if not initial_observation.wait(5): raise RuntimeError('startup observation missing')
    return real_run(config)
dispatch._process_record, dispatch.run_agent = record, run
raise SystemExit(dispatch._detached(sys.argv[2]))
'''
            spec = replace(spec, argv=(*spec.argv[:2], bootstrap, *spec.argv[3:]))
        return real_start(supervisor, spec, run_id=run_id, attempt_id=attempt_id)
    monkeypatch.setattr(ProcessSupervisor, 'start', start_after_initial_observation)
    try:
        result = dispatch_worker(config)
        deadline = time.monotonic() + 5
        while not (tmp_path / 'paid-attempts').is_file() or (tmp_path / 'paid-attempts').read_text().splitlines() != ['one']:
            assert time.monotonic() < deadline, 'provider did not execute'
            time.sleep(.01)
        assert (tmp_path / 'paid-attempts').read_text().splitlines() == ['one']
        assert result.state == 'running', result.reason
        process = json.loads(Path(str(config.state_file) + '.process.json').read_text())
        assert result.identity == process['identity']
        assert dispatch.inspect_worker(config).liveness == 'alive'
        with pytest.raises(RuntimeError, match='prohibits relaunch'):
            dispatch_worker(config)
        assert (tmp_path / 'paid-attempts').read_text().splitlines() == ['one']
    finally:
        (tmp_path / 'release-provider').touch()
        if Path(str(config.state_file) + '.process.json').exists():
            assert dispatch._stop_provider(config).state == 'dead'
        wrapper_path = Path(str(config.state_file) + '.dispatcher.process.json')
        if wrapper_path.exists():
            wrapper = json.loads(wrapper_path.read_text())
            assert ProcessSupervisor(Path(wrapper['record_directory'])).stop(
                ProcessIdentity(**wrapper['identity']), grace_seconds=.1).state == 'dead'


def test_bootstrap_uncertain_shutdown_is_unknown_and_prohibits_relaunch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ai_skills.processes import Liveness, ProcessIdentity, ProcessSupervisor, StopResult
    config = worker_config(tmp_path, '', detach=True)
    class InaccessibleOwner(ProcessSupervisor):
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


def test_runner_return_without_confirmed_tree_death_stays_nonterminal(tmp_path,monkeypatch):
    from ai_skills.processes import Liveness,ProcessIdentity,ProcessSupervisor
    from ai_skills.runner import AgentRunResult
    config=worker_config(tmp_path,'pass')
    def fake_run(run):
        identity=ProcessIdentity(987654321,1,socket.gethostname(),run.run_id,run.attempt_id)
        write_json_atomic(Path(run.process_state_file),{'identity':identity.__dict__,'record_directory':str(tmp_path/'records')})
        return AgentRunResult(0,run.agent,run.model)
    monkeypatch.setattr(dispatch,'run_agent',fake_run)
    monkeypatch.setattr(ProcessSupervisor,'inspect',lambda self,identity:Liveness('unknown',reason='inaccessible'))
    result=dispatch_worker(config)
    assert (result.state,result.return_code)==('unknown',3)
    state=json.loads(config.state_file.read_text())
    assert state.get('alive') is not False
    assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='pending'
    assert not config.agent_run.done_file.exists()


def test_confirmed_interrupt_records_interrupted_outcome(tmp_path,monkeypatch):
    from ai_skills.processes import ProcessIdentity,ProcessSupervisor,StopResult
    config=worker_config(tmp_path,'''import os,time
from pathlib import Path
Path('ready').touch()
while not Path('release').exists():time.sleep(.01)
''',hard_limit_seconds=.1)
    real_stop=dispatch._stop_provider
    def interrupted(config,grace=.1):
        result=real_stop(config,grace)
        assert result.state=='dead'
        raise KeyboardInterrupt
    monkeypatch.setattr(dispatch,'_stop_provider',interrupted)
    # The second stop confirms the already stopped tree without another signal.
    calls=[0]
    def stop(config,grace=.1):
        calls[0]+=1
        return interrupted(config,grace) if calls[0]==1 else real_stop(config,grace)
    monkeypatch.setattr(dispatch,'_stop_provider',stop)
    result=dispatch_worker(config)
    assert (result.state,result.return_code,result.reason)==('interrupted',130,'interrupted')
    outcome=json.loads(dispatch.outcome_path(config.state_file).read_text())
    assert outcome['status']=='interrupted' and outcome['worker_return_code']==130
    assert dispatch.inspect_worker(config).liveness=='dead'


@pytest.mark.parametrize('liveness',['alive','unknown'])
def test_adoption_cannot_repair_terminal_publication_under_live_or_unknown_wrapper(tmp_path,monkeypatch,liveness):
    from ai_skills.processes import Liveness,ProcessIdentity,ProcessSupervisor
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    dispatch_worker(config)
    record=json.loads(Path(str(config.state_file)+'.config.json').read_text())
    run=record['agent_run']
    wrapper=ProcessIdentity(987654321,1,socket.gethostname(),run['run_id']+'-dispatcher',run['attempt_id'])
    write_json_atomic(Path(str(config.state_file)+'.dispatcher.process.json'),{'identity':wrapper.__dict__,'record_directory':str(tmp_path/'records')})
    config.agent_run.done_file.unlink()
    real_inspect=ProcessSupervisor.inspect
    monkeypatch.setattr(ProcessSupervisor,'inspect',lambda self,identity:Liveness(liveness) if identity.pid==wrapper.pid else real_inspect(self,identity))
    result=dispatch.adopt_worker(config.state_file)
    assert result.state==('running' if liveness=='alive' else 'unknown')
    assert not config.agent_run.done_file.exists()


@pytest.mark.parametrize(('forced','status','code'),[('hard-limit-exceeded','failed',124),('interrupted','interrupted',130)])
def test_forced_stop_survives_terminal_outcome_write_failure_with_native_provider_exit(tmp_path,monkeypatch,forced,status,code):
    """POSIX handles SIGTERM with exit 0; Windows terminates the Job with exit 1."""
    expected_provider_code=1 if os.name=='nt' else 0
    config=worker_config(tmp_path,'''import json,os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,lambda *_:exit(0))
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')
Path('provider-ready').touch()
while True:time.sleep(.01)
''',hard_limit_seconds=.4)
    real_write=dispatch.write_json_atomic
    def fail_terminal_outcome(path,value):
        if str(path).endswith('.outcome.json') and value.get('status')!='pending':
            raise OSError('injected terminal outcome failure')
        return real_write(path,value)
    monkeypatch.setattr(dispatch,'write_json_atomic',fail_terminal_outcome)
    real_stop=dispatch._stop_provider
    calls=[0]
    def stop(selected,grace=.1):
        calls[0]+=1
        if forced=='interrupted' and calls[0]==1:raise KeyboardInterrupt
        return real_stop(selected,grace)
    monkeypatch.setattr(dispatch,'_stop_provider',stop)
    with pytest.raises(OSError,match='injected terminal outcome'):dispatch_worker(config)
    assert (tmp_path/'provider-ready').exists()
    saved=dispatch.WorkerConfig.from_record(json.loads(Path(str(config.state_file)+'.config.json').read_text()))
    process,supervisor=dispatch._provider_record(saved)
    from ai_skills.processes import ProcessIdentity
    assert supervisor.return_code(ProcessIdentity(**process['identity']))==expected_provider_code
    assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='pending'
    monkeypatch.setattr(dispatch,'write_json_atomic',real_write)
    state=json.loads(config.state_file.read_text())
    state['launcher_identity']['created_at']+=1  # Prior dispatcher lifetime has died.
    real_write(config.state_file,state)
    result=dispatch.adopt_worker(config.state_file)
    assert (result.state,result.return_code,result.reason)==(status,code,forced)
    outcome=json.loads(dispatch.outcome_path(config.state_file).read_text())
    assert (outcome['status'],outcome['worker_return_code'],outcome['provider_return_code'],outcome['reason'])==(status,code,expected_provider_code,forced)
    assert json.loads(config.state_file.read_text())['stop_intent']['reason']==forced
    assert f'status={status}' in config.agent_run.done_file.read_text()
    assert 'type=dispatch-complete|' not in config.agent_run.events_log.read_text()


@pytest.mark.parametrize(('owner','expected','reason'),[('alive','running','launcher-ownership-alive'),('unknown','unknown','launcher-ownership-unknown')])
def test_active_foreground_dispatcher_keeps_finalization_ownership_after_provider_exit(tmp_path,monkeypatch,owner,expected,reason):
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    real_finalize=dispatch._finalize_attempt
    checked=[False]
    def finalize(selected,**kwargs):
        if not checked[0]:
            checked[0]=True
            before=json.loads(selected.state_file.read_text())
            if owner=='unknown':
                changed={**before,'launcher_identity':{**before['launcher_identity'],'host':'unreachable-host'}}
                write_json_atomic(selected.state_file,changed)
            script='import json,sys;sys.path.insert(0,sys.argv[1]);from dataclasses import asdict;from ai_skills.dispatch import adopt_worker;print(json.dumps(asdict(adopt_worker(sys.argv[2])),default=str))'
            child=subprocess.run([sys.executable,'-c',script,str(Path(dispatch.__file__).resolve().parents[1]),str(selected.state_file)],capture_output=True,text=True,check=True)
            result=json.loads(child.stdout)
            assert (result['state'],result['reason'])==(expected,reason)
            write_json_atomic(selected.state_file,before)
            assert json.loads(dispatch.outcome_path(selected.state_file).read_text())['status']=='pending'
            assert not selected.agent_run.done_file.exists()
        return real_finalize(selected,**kwargs)
    monkeypatch.setattr(dispatch,'_finalize_attempt',finalize)
    assert dispatch_worker(config).state=='completed'
    assert dispatch.adopt_worker(config.state_file).state=='completed'


def test_stop_intent_storage_failure_does_not_signal_provider(tmp_path,monkeypatch):
    config=worker_config(tmp_path,'import time;time.sleep(20)',hard_limit_seconds=.15)
    real_write,real_stop=dispatch.write_json_atomic,dispatch._stop_provider
    def fail_intent(path,value):
        if 'stop_intent' in value:raise OSError('injected stop intent failure')
        return real_write(path,value)
    monkeypatch.setattr(dispatch,'write_json_atomic',fail_intent)
    monkeypatch.setattr(dispatch,'_stop_provider',lambda *args,**kwargs:pytest.fail('signal before durable intent'))
    try:
        with pytest.raises(OSError,match='injected stop intent'):dispatch_worker(config)
        assert dispatch.inspect_worker(config).liveness=='alive'
        assert json.loads(dispatch.outcome_path(config.state_file).read_text())['status']=='pending'
        assert not config.agent_run.done_file.exists()
    finally:
        monkeypatch.setattr(dispatch,'write_json_atomic',real_write)
        monkeypatch.setattr(dispatch,'_stop_provider',real_stop)
        if Path(str(config.state_file)+'.process.json').exists():real_stop(config)


@pytest.mark.parametrize('field',['run_id','attempt_id','state_file','status','worker_return_code','provider_identity'])
def test_corrupt_stop_intent_is_not_replaced_or_accepted_as_completion(tmp_path,field):
    config=worker_config(tmp_path,"import os;from pathlib import Path;Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text('{}')")
    dispatch_worker(config)
    selected=dispatch.WorkerConfig.from_record(json.loads(Path(str(config.state_file)+'.config.json').read_text()))
    state=json.loads(config.state_file.read_text())
    intent={'schema_version':1,'run_id':selected.agent_run.run_id,'attempt_id':selected.agent_run.attempt_id,
        'issue':'7','role':'fixture','state_file':str(selected.state_file),'recorded_at':time.time(),
        'reason':'hard-limit-exceeded','status':'failed','worker_return_code':124,
        'provider_identity':state['provider_identity']}
    intent[field]=999 if field=='worker_return_code' else {} if field=='provider_identity' else 'foreign'
    state['stop_intent']=intent
    write_json_atomic(config.state_file,state)
    before=config.state_file.read_bytes()
    result=dispatch.adopt_worker(config.state_file)
    assert (result.state,result.return_code)==('unknown',3)
    assert config.state_file.read_bytes()==before


@pytest.mark.parametrize('provider_reason',['cannot-proceed','artifact-recovery-required'])
def test_forced_stop_decision_cannot_be_replaced_by_typed_failure_reason(tmp_path,provider_reason):
    """Native provider exit is POSIX handler 0 or Windows Job termination 1."""
    expected_provider_code=1 if os.name=='nt' else 0
    config=worker_config(tmp_path,'''import json,os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,lambda *_:exit(0))
payload={'schema_version':1,'artifact_kind':'role_failure','run_id':os.environ['RUN_WITH_IT_RUN_ID'],'attempt_id':os.environ['RUN_WITH_IT_ATTEMPT_ID'],'issue':'7','role':'plan','status':'blocked','reason':REASON,'summary':'Provider could not continue'}
Path(os.environ['RUN_WITH_IT_RESULT_FILE']).write_text(json.dumps(payload))
while True:time.sleep(.01)
'''.replace('REASON',repr(provider_reason)),hard_limit_seconds=.4)
    config=replace(config,agent_run=config.agent_run.replace(role='plan',issue_dir=tmp_path))
    result=dispatch_worker(config)
    assert (result.state,result.return_code,result.reason)==('failed',124,'hard-limit-exceeded')
    outcome=json.loads(dispatch.outcome_path(config.state_file).read_text())
    assert (outcome['status'],outcome['provider_return_code'],outcome['worker_return_code'])==('failed',expected_provider_code,124)
    payload=json.loads(config.agent_run.result_file.read_text())
    assert payload['status']=='blocked' and payload['reason']==provider_reason
    assert not (tmp_path/'plan.md').exists()


def test_hard_limit_rejected_capture_keeps_salvage_and_recovery_budget(tmp_path):
    from ai_skills.recovery import RecoveryEvidence, RecoveryPolicy, decide_recovery
    from test_dispatch import CAPTURE_CHECKS, capture_worker
    body = '''
capture = Path(args[-2])
Path('feature.txt').write_text('uncommitted progress')
raw = json.dumps({**metadata['result_capture']['envelope'], 'result': {'status': 'success'}}).encode()
capture.write_bytes(raw)
result_path.with_name('capture-original').write_bytes(raw)
time.sleep(60)
'''
    config = capture_worker(tmp_path, body, result_capture=CAPTURE_CHECKS, hard_limit_seconds=.5)
    result = dispatch_worker(config)
    assert (result.state, result.return_code, result.reason) == (
        'artifact-recovery-required', 75, 'artifact-recovery-required')
    state = json.loads(config.state_file.read_text())
    saved = json.loads(Path(str(config.state_file) + '.config.json').read_text())
    outcome = json.loads(Path(str(config.state_file) + '.outcome.json').read_text())
    assert state['stop_intent']['reason'] == 'hard-limit-exceeded'
    assert outcome['reason'] == 'artifact-recovery-required' and outcome['worker_return_code'] == 75
    assert outcome['git_progress']['head'] != saved['pre_spawn_head']
    report = json.loads(config.agent_run.result_file.read_text())
    assert report['status'] == 'artifact_recovery_required'
    assert 'dispatcher committed the dirty tree' in json.dumps(report)
    assert Path(saved['agent_run']['final_response_file']).read_bytes() == config.agent_run.result_file.with_name('capture-original').read_bytes()
    event, = [line for line in config.agent_run.events_log.read_text().splitlines()
              if 'type=dispatch-capture-rejected|' in line]
    for part in (f"run_id={saved['agent_run']['run_id']}", f"attempt_id={saved['agent_run']['attempt_id']}",
                 'issue=7', 'role=impl', 'cycle=4', 'reason=capture-attempt-not-successful'):
        assert part in event
    evidence = RecoveryEvidence(issue='7', sub_state={'phase': 'impl'}, workers=((
        {'role': 'impl', 'cycle': 4}, {'state': result.state}, True, True, {'alive': False}),))
    policy = RecoveryPolicy()
    decision = decide_recovery(evidence, policy)
    assert (decision.action, decision.reason) == ('spawn_recovery', 'artifact-recovery-required')
    assert decision.details['max_recovery_attempts'] == policy.max_attempts
