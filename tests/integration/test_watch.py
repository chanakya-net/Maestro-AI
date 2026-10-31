import json
from pathlib import Path
import sys

from ai_skills.dispatch import WatchConfig, watch_run
from ai_skills.processes import ProcessSupervisor, ProcessSpec
from ai_skills.storage import write_json_atomic
from dataclasses import asdict


def test_watch_byte_cursor_retains_partial_event_and_resets_after_replacement(tmp_path):
    events=tmp_path/'events'; events.write_bytes('STATUS|type=heartbeat|progress=λ\npartial'.encode())
    state=tmp_path/'pool.json'; state.write_text('{}')
    config=WatchConfig(events_log=events,pool_state_file=state,cursor_file=tmp_path/'cursor',wait_seconds=0,poll_seconds=.01)
    result=watch_run(config)
    assert result.result=='pool-dead'
    assert result.lines==('STATUS|type=heartbeat|progress=λ',)
    cursor=json.loads(config.cursor_file.read_text())
    assert cursor['schema_version']==1
    assert cursor['offset']==len('STATUS|type=heartbeat|progress=λ\n'.encode())
    with events.open('ab') as stream: stream.write(b' completed\n')
    assert watch_run(config).lines==('partial completed',)
    replacement=tmp_path/'replacement'; replacement.write_text('STATUS|type=pool-empty\n'); replacement.replace(events)
    assert watch_run(config).result=='pool-empty'
    assert watch_run(config).result=='pool-empty'


def test_bounded_watch_live_identity_then_truncated_log(tmp_path):
    supervisor=ProcessSupervisor(tmp_path/'processes')
    with supervisor.start(ProcessSpec((sys.executable,'-c','import time;time.sleep(20)'),tmp_path,{}),run_id='pool',attempt_id='one') as process:
        state=tmp_path/'pool.json'; write_json_atomic(state,{'identity':asdict(process.identity),'record_directory':str(supervisor.directory)})
        events=tmp_path/'events'; events.write_text('STATUS|type=first-long-event\n')
        config=WatchConfig(events_log=events,pool_state_file=state,cursor_file=tmp_path/'cursor',wait_seconds=.05,poll_seconds=.01)
        assert watch_run(config).result=='running'
        events.write_text('STATUS|type=new\n')
        result=watch_run(config)
        assert result.result=='running'
        assert result.lines==('STATUS|type=new',)


def test_foreground_pool_lease_uses_exact_same_host_creation_time(tmp_path):
    import os
    import psutil
    import socket
    state=tmp_path/'pool.json'
    lease={'engine':'python','state':'running','pool_pid':os.getpid(),
           'pool_created_at':psutil.Process().create_time(),'host':socket.gethostname()}
    write_json_atomic(state,lease)
    events=tmp_path/'events';events.touch()
    config=WatchConfig(events_log=events,pool_state_file=state,cursor_file=tmp_path/'cursor',wait_seconds=0,poll_seconds=.01)
    assert watch_run(config).result=='running'
    lease['pool_created_at']+=1
    write_json_atomic(state,lease)
    assert watch_run(config).result=='pool-dead'


def test_pool_empty_text_inside_progress_does_not_forge_terminal_event(tmp_path):
    events=tmp_path/'events';events.write_text('STATUS|type=heartbeat|progress=waiting for type=pool-empty\n')
    state=tmp_path/'pool.json';state.write_text('{}')
    result=watch_run(WatchConfig(events,state,tmp_path/'cursor',0,.01))
    assert result.result=='pool-dead'
