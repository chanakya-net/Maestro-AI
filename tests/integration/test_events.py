from ai_skills.events import EventBus, emit_status


def test_status_fields_cannot_inject_records():
    assert emit_status('heartbeat', {'issue': '5\r\nSTATUS|bad=x', 'role': ''}) == 'STATUS|type=heartbeat|issue=5  STATUS/bad=x|role=unknown'


def test_bus_forwards_status_and_suppresses_only_heartbeat_console(tmp_path):
    bus = EventBus(status_file=tmp_path/'status', events_log=tmp_path/'events', log_file=tmp_path/'log')
    bus.forward('STATUS|type=heartbeat|issue=5', 'stdout')
    bus.forward('ROUTE|agent=codex', 'stderr')
    assert (tmp_path/'status').read_text() == 'ROUTE|agent=codex\n'
    assert (tmp_path/'events').read_text().splitlines() == ['STATUS|type=heartbeat|issue=5', 'ROUTE|agent=codex']
    assert (tmp_path/'log').read_text().splitlines() == ['STATUS|type=heartbeat|issue=5', 'ROUTE|agent=codex']


def test_final_unterminated_status_is_forwarded_and_log_records_stay_separate(tmp_path, capsys):
    bus = EventBus(events_log=tmp_path/'events',log_file=tmp_path/'log')
    bus.forward('STATUS|type=heartbeat|progress=last', 'stdout',newline=False)
    bus.emit('agent-complete',{'status':'success'})
    assert (tmp_path/'events').read_text().splitlines() == [
        'STATUS|type=heartbeat|progress=last','STATUS|type=agent-complete|status=success']
    assert (tmp_path/'log').read_text().splitlines() == [
        'STATUS|type=heartbeat|progress=last','STATUS|type=agent-complete|status=success']
    assert 'heartbeat' not in capsys.readouterr().out


def test_concurrent_process_event_records_do_not_interleave(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root=str(Path(__file__).resolve().parents[2]/'src')
    source='''import sys
sys.path.insert(0,sys.argv[1])
from ai_skills.events import EventBus
bus=EventBus(events_log=sys.argv[2])
for index in range(8): bus.emit('fixture', {'writer':sys.argv[3], 'payload':'x'*12000, 'index':index})
'''
    processes=[subprocess.Popen([sys.executable,'-c',source,root,str(tmp_path/'events'),str(writer)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for writer in range(4)]
    assert [process.wait(timeout=15) for process in processes] == [0]*4
    lines=(tmp_path/'events').read_text().splitlines()
    assert len(lines)==32
    assert all(line.count('STATUS|')==1 and '|payload='+'x'*12000+'|index=' in line for line in lines)


def test_portable_emit_cli_persists_coordinator_status_once(tmp_path):
    import json
    from ai_skills.dispatch import main
    fields=tmp_path/'fields.json';fields.write_text(json.dumps({'issue':'7','role':'sub-coord','status':'running'}))
    assert main(['emit','--type','phase','--fields-file',str(fields),'--log-file',str(tmp_path/'log'),
                 '--status-file',str(tmp_path/'status'),'--events-log',str(tmp_path/'events')])==0
    expected='STATUS|type=phase|issue=7|role=sub-coord|status=running\n'
    assert (tmp_path/'log').read_text()==expected
    assert (tmp_path/'status').read_text()==expected
    assert (tmp_path/'events').read_text()==expected
