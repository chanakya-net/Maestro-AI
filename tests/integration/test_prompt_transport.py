"""Real subprocess regressions for full-size, non-argv provider prompts."""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from ai_skills.processes import ProcessSpec, ProcessSupervisor
from ai_skills.resources import resource_path
from ai_skills.runner import AgentRunConfig, build_invocation, load_registry, run_agent


def configuration(tmp_path, agent='codex', *, response="print('done')", help_text='--model\n--input-format\n--output-format'):
    script = tmp_path/'provider.py'
    script.write_text(f'''import json, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8')
if '--help' in sys.argv:
 print({help_text!r}); raise SystemExit(0)
data = sys.stdin.buffer.read()
pathlib.Path('received.bin').write_bytes(data)
pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
''' + response, encoding='utf-8')
    registry = load_registry()
    invocation = registry['agents'][agent]['invocation']
    invocation['command'] = sys.executable
    invocation['args_template'].insert(0, str(script))
    registry_file = tmp_path/'registry.json'
    registry_file.write_text(json.dumps(registry), encoding='utf-8')
    context, prompt = tmp_path/'context.md', tmp_path/'prompt.md'
    context.write_text('Context λ &|<>^%!"\n', encoding='utf-8')
    prompt.write_text('你好🙂 &|<>^%!" C:\\tail\\\n'*16000, encoding='utf-8')
    return AgentRunConfig(agent=agent, context_file=context, prompt_file=prompt, repo_root=tmp_path,
                          registry_file=registry_file, unattended=True, gui_mode='0', environment=dict(os.environ),
                          record_directory=tmp_path/'records', stream_directory=tmp_path/'streams',
                          process_state_file=tmp_path/'state.json', events_log=tmp_path/'events',
                          log_file=tmp_path/'log', heartbeat_seconds=0)


@pytest.mark.parametrize('agent', ['codex', 'claude', 'opencode', 'agy'])
def test_large_full_payload_uses_provider_stdin_without_argv_or_json_record_leak(tmp_path, agent):
    response = "print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'done'}}))" if agent == 'agy' else "print('done')"
    config = configuration(tmp_path, agent, response=response)
    expected = 'Context λ &|<>^%!"\n\nInstructions:\n\n' + ('你好🙂 &|<>^%!" C:\\tail\\\n'*16000).rstrip('\n')
    assert len(expected.encode('utf-8')) > 200000
    assert run_agent(config).return_code == 0
    received = (tmp_path/'received.bin').read_bytes()
    if agent == 'agy':
        assert json.loads(received) == {'event':'user','message':{'content':expected}}
    else:
        assert received == expected.encode('utf-8')
    assert max(map(len, json.loads((tmp_path/'argv.json').read_text()))) < 4096
    for record in (tmp_path/'records').glob('*.json'):
        assert '你好' not in record.read_text(encoding='utf-8')
        assert 'stdin' in record.read_text(encoding='utf-8')
    assert list((tmp_path/'records').glob('*.stdin')) == []


def test_packaged_coordinator_prompt_is_passed_whole_to_codex_style_stdin(tmp_path):
    config = configuration(tmp_path, response="assert sys.argv[-1] == '-'; print('done')")
    config = config.replace(prompt_file=resource_path('sub-coordinator-prompt.md'))
    prompt = config.prompt_file.read_text(encoding='utf-8')
    assert len(prompt.encode('utf-8')) > 80000
    assert run_agent(config).return_code == 0
    assert (tmp_path/'received.bin').read_bytes() == ('Context λ &|<>^%!"\n\nInstructions:\n\n'+prompt+'\n').rstrip('\n').encode('utf-8')


def test_owned_input_survives_caller_adoption_and_stops_by_identity(tmp_path):
    marker = tmp_path/'received'
    code = "import sys,time;from pathlib import Path;time.sleep(.3);Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read());time.sleep(30)"
    payload = ('秘密🙂\n'*40000).encode('utf-8')
    owner = ProcessSupervisor(tmp_path/'records')
    owned = owner.start(ProcessSpec((sys.executable, '-c', code, str(marker)), tmp_path, {}, stdin=payload), run_id='run', attempt_id='input')
    try:
        adopted = ProcessSupervisor(tmp_path/'records')
        deadline = time.monotonic()+8
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.03)
        assert marker.read_bytes() == payload
        assert adopted.inspect(owned.identity).state == 'alive'
        assert list((tmp_path/'records').glob('*.stdin'))
        assert adopted.stop(owned.identity, grace_seconds=.1).state == 'dead'
        assert list((tmp_path/'records').glob('*.stdin')) == []
    finally:
        owned.close()


def test_agy_authoritative_result_restores_multiline_status_and_markdown(tmp_path, capsys):
    narrative = '# Result λ\n\n```json\n{"decision":"ship"}\n```\nSTATUS|type=artifact-written|path=result.md\n'
    events = [{'event':'step_update','step_update':{'text_delta':'untrusted partial'}}, {'event':'result','result':{'status':'SUCCESS','response':narrative}}]
    config = configuration(tmp_path, 'agy', response=f"for event in {events!r}: print(json.dumps(event, ensure_ascii=False))")
    config = config.replace(model='claude-sonnet-5-5-high')
    assert run_agent(config).return_code == 0
    argv = json.loads((tmp_path/'argv.json').read_text(encoding='utf-8'))
    model_flag = argv.index('--model')
    assert argv[model_flag:model_flag + 2] == ['--model', 'claude-sonnet-5-5-high']
    assert argv[-4:] == ['--input-format', 'stream-json', '--output-format', 'stream-json']
    output = capsys.readouterr().out
    assert output == narrative
    assert 'type=artifact-written|path=result.md' in config.events_log.read_text()
    assert '"event": "result"' in (config.stream_directory/'stdout').read_text()


def test_old_agy_reports_actionable_infrastructure_unavailability_without_provider_run(tmp_path):
    config = configuration(tmp_path, 'agy', response="raise AssertionError('must not invoke unsupported CLI')")
    script = tmp_path/'provider.py'
    script.write_text("import sys\nif '--help' in sys.argv: print('--print'); raise SystemExit(0)\nraise AssertionError('provider invocation')", encoding='utf-8')
    result = run_agent(config)
    assert result.return_code != 0
    assert result.unavailable_reason == 'cli-unsupported'
    assert 'upgrade' in config.log_file.read_text().lower()
    assert not (tmp_path/'received.bin').exists()


@pytest.mark.parametrize('missing_flag', ['--model', '--input-format', '--output-format'])
def test_agy_missing_required_flag_fails_before_inference(tmp_path, missing_flag):
    help_text = '\n'.join(flag for flag in ('--model', '--input-format', '--output-format') if flag != missing_flag)
    config = configuration(tmp_path, 'agy', response="raise AssertionError('inference must not start')", help_text=help_text)

    result = run_agent(config)

    assert result.return_code != 0
    assert result.unavailable_reason == 'cli-unsupported'
    assert missing_flag in config.log_file.read_text().lower()
    assert 'upgrade' in config.log_file.read_text().lower()
    assert not (tmp_path/'received.bin').exists()


MISLEADING_HELP = {
    '--model': ('  --input-format <FORMAT>   stream-json input\n'
                '  --output-format <FORMAT>  stream-json output\n'
                '  --model-name <NAME>       model name, not --model\n'),
    '--input-format': ('  --model <NAME>            exact model\n'
                       '  --output-format <FORMAT>  stream-json output\n'
                       '  --input-formatting <FMT>  like --input-format but different\n'),
    '--output-format': ('  --model <NAME>            exact model\n'
                        '  --input-format <FORMAT>   stream-json input, emits via --output-format\n'
                        '  --output-formatting <FMT> like --output-format but different\n'),
}


@pytest.mark.parametrize('missing_flag', ['--model', '--input-format', '--output-format'])
def test_agy_longer_option_or_description_text_does_not_satisfy_missing_flag(tmp_path, missing_flag):
    config = configuration(tmp_path, 'agy', response="raise AssertionError('inference must not start')",
                           help_text=MISLEADING_HELP[missing_flag])

    result = run_agent(config)

    assert result.return_code != 0
    assert result.unavailable_reason == 'cli-unsupported'
    assert missing_flag in config.log_file.read_text().lower()
    assert not (tmp_path/'received.bin').exists()


@pytest.mark.parametrize('help_text', [
    '  -m, --model=<NAME>  exact model\n  --input-format <FORMAT>  stream-json\n  --output-format <FORMAT>  stream-json\n',
])
def test_agy_advertised_option_syntax_satisfies_required_flags(tmp_path, help_text):
    config = configuration(tmp_path, 'agy', help_text=help_text)

    result = run_agent(config)

    assert result.unavailable_reason != 'cli-unsupported'
    assert (tmp_path/'argv.json').exists()


@pytest.mark.parametrize('extra_args', [('--model', 'other-model'), ('--model=other-model',)])
def test_agy_conflicting_extra_model_fails_before_inference(tmp_path, extra_args):
    from dataclasses import replace

    config = configuration(tmp_path, 'agy', response="raise AssertionError('inference must not start')")
    config = replace(config, extra_args=extra_args)

    with pytest.raises(ValueError, match='AGY.*--model'):
        run_agent(config)

    assert not (tmp_path/'received.bin').exists()


def test_input_survives_actual_launching_caller_exit(tmp_path):
    import subprocess
    from ai_skills.processes import ProcessIdentity
    marker = tmp_path/'received'
    code = "import sys,time;from pathlib import Path;time.sleep(.5);Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read());time.sleep(30)"
    source = '''import json,sys
from dataclasses import asdict
from pathlib import Path
from ai_skills.processes import ProcessSpec,ProcessSupervisor
root=Path(sys.argv[1])
supervisor=ProcessSupervisor(root/'records')
owned=supervisor.start(ProcessSpec((sys.executable,'-c',sys.argv[2],str(root/'received')),root,{},stdin=sys.stdin.buffer.read()),run_id='detached',attempt_id='input')
print(json.dumps(asdict(owned.identity)))
'''
    payload = ('Detached 秘密🙂 &|<>^%!"\n'*16000).encode('utf-8')
    launched = subprocess.run((sys.executable, '-c', source, str(tmp_path), code), input=payload, capture_output=True, check=True, timeout=15)
    identity = ProcessIdentity(**json.loads(launched.stdout))
    supervisor = ProcessSupervisor(tmp_path/'records')
    try:
        record = supervisor.lookup_attempt('detached', 'input')
        input_path = Path(record['stdin_file'])
        assert input_path.read_bytes() == payload
        assert '秘密' not in json.dumps(record, ensure_ascii=False)
        if os.name != 'nt':
            assert input_path.stat().st_mode & 0o777 == 0o600
        deadline = time.monotonic()+8
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.03)
        assert marker.read_bytes() == payload
        assert supervisor.stop(identity, grace_seconds=.1).state == 'dead'
        assert not input_path.exists()
    finally:
        supervisor.stop(identity, grace_seconds=.1)


def test_input_is_removed_after_provider_startup_failure(tmp_path):
    supervisor = ProcessSupervisor(tmp_path/'records')
    with pytest.raises(RuntimeError, match='launch'):
        supervisor.start(ProcessSpec((str(tmp_path/'missing-provider'),), tmp_path, {}, stdin=b'private prompt'), run_id='run', attempt_id='fail')
    assert not list((tmp_path/'records').glob('*.stdin'))
    assert all('private prompt' not in record.read_text() for record in (tmp_path/'records').glob('*.json'))


def test_codex_capture_probe_is_bounded_and_flag_gated(tmp_path, monkeypatch):
    import subprocess

    from ai_skills.runner import supports_final_response_capture

    script = tmp_path / 'exec'
    spec = ProcessSpec((sys.executable, 'exec', '-'), tmp_path, dict(os.environ))
    for flag, supported in (('--output-last-message <FILE>', True),
                            ('--output-last-message-other', False), ('old CLI', False)):
        script.write_text(f'import sys\nassert sys.argv[1:] == ["--help"]\nprint({flag!r})\n', encoding='utf-8')
        assert supports_final_response_capture(spec) is supported

    def timed_out(argv, **kwargs):
        assert argv[-2:] == ('exec', '--help')
        assert 0 < kwargs['timeout'] <= 10
        assert kwargs['stdin'] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', timed_out)
    assert not supports_final_response_capture(spec)


def test_fake_codex_receives_capture_flag_and_full_stdin_payload(tmp_path):
    capture = tmp_path / 'streams' / 'final-response.json'
    config = configuration(tmp_path, response="""
assert sys.argv[-3] == '--output-last-message' and sys.argv[-1] == '-'
pathlib.Path(sys.argv[-2]).write_text('last response', encoding='utf-8')
""")
    config = config.replace(role='impl', dispatcher_managed=True, state_file=tmp_path / 'worker.json',
        result_file=tmp_path / 'result.json', final_response_file=capture,
        result_capture_context={'envelope': {'cycle': '4'}})
    assert run_agent(config).return_code == 0
    received = (tmp_path / 'received.bin').read_bytes()
    expected = 'Context λ &|<>^%!"\n\nInstructions:\n\n' + config.prompt_file.read_text(encoding='utf-8').rstrip('\n')
    assert received.startswith(expected.encode('utf-8'))
    metadata = json.loads(received.decode('utf-8').rsplit('Authoritative attempt metadata:\n', 1)[1])
    assert metadata['result_capture'] == config.result_capture_context
    assert max(map(len, json.loads((tmp_path / 'argv.json').read_text(encoding='utf-8')))) < 4096
    assert capture.read_text(encoding='utf-8') == 'last response'


@pytest.mark.parametrize(('agent', 'source', 'wire'), [
    ('claude', 'claude-json',
     {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'errors': ['usage limit reached']}),
    ('opencode', 'opencode-json-v1.16.2',
     {'type': 'error', 'sessionID': 'session', 'error': {'message': 'failed to authenticate'}}),
])
@pytest.mark.parametrize('code', [0, 2])
def test_stream_capture_preserves_provider_unavailability(tmp_path, monkeypatch, agent, source, wire, code):
    from ai_skills import runner
    config = configuration(tmp_path, agent, response=f'print(json.dumps({wire!r})); raise SystemExit({code})')
    if agent == 'opencode':
        registry = json.loads(config.registry_file.read_bytes())
        invocation = registry['agents'][agent]['invocation']
        script = invocation['args_template'].pop(0)
        invocation['command'] = 'opencode'
        config.registry_file.write_text(json.dumps(registry), encoding='utf-8')
        monkeypatch.setattr(runner, 'resolve_executable', lambda argv, env: (sys.executable, script, *argv[1:]))
    config = config.replace(role='impl', dispatcher_managed=True, state_file=tmp_path / 'worker.json',
        result_file=tmp_path / 'result.json', result_capture_context={'source': source})
    result = run_agent(config)
    assert result.return_code == (code or 1)
    assert result.unavailable_reason == ('quota' if agent == 'claude' else 'auth')
    assert json.loads((tmp_path / 'streams' / 'stdout').read_bytes()) == wire
    assert 'agent-unavailable' in config.events_log.read_text(encoding='utf-8')
    assert 'failed to authenticate' not in config.log_file.read_text(encoding='utf-8')
