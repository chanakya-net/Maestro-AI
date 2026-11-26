"""Real subprocess regressions for full-size, non-argv provider prompts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import pytest

from ai_skills.artifacts import log_signals_unavailable_route
from ai_skills.processes import ProcessSpec, ProcessSupervisor
from ai_skills.resources import resource_path
from ai_skills.runner import AgentRunConfig, build_invocation, load_registry, run_agent


def configuration(tmp_path, agent='codex', *, response="print('done')", help_text='--model\n--input-format\n--output-format'):
    script = tmp_path/'provider.py'
    script.write_text(f'''import json, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('calls.log', 'a', encoding='utf-8') as calls: calls.write(('probe' if '--help' in sys.argv else 'run') + '\\n')
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
    assert len(prompt.encode('utf-8')) > 20000
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


GEMINI_4_MODELS = (
    *(f'gemini-4-argon-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'gemini-4-{family}-{effort}' for family in ('neon', 'hydrogen') for effort in ('low', 'medium', 'high', 'xhigh')),
    *(f'gemini-4-carbon-{effort}' for effort in ('low', 'medium', 'high')),
)


@pytest.mark.parametrize('model', GEMINI_4_MODELS)
def test_fake_agy_receives_each_gemini_4_model_once_with_unicode_stream(tmp_path, capsys, model):
    narrative = f'結果 🙂 {model}\n'
    events = [{'event': 'result', 'result': {'status': 'SUCCESS', 'response': narrative}}]
    config = configuration(tmp_path, 'agy', response=f"for event in {events!r}: print(json.dumps(event, ensure_ascii=False))")
    assert run_agent(config.replace(model=model)).return_code == 0
    argv = json.loads((tmp_path/'argv.json').read_text(encoding='utf-8'))
    assert argv.count('--model') == 1
    assert argv[argv.index('--model') + 1] == model
    assert '你好🙂' in (tmp_path/'received.bin').read_text(encoding='utf-8')
    assert capsys.readouterr().out == narrative


def test_extract_response_handles_xhigh_agy_stream():
    import io
    from ai_skills.provider_capture import extract_response
    cid = 'conv-1'
    step = lambda index, kind, state: {'event': 'step_update', 'step_update': {
        'conversation_id': cid, 'step_index': index, 'state': state, 'step_type': kind}}
    events = [
        {'event': 'init', 'conversation_id': cid, 'init': {'model': 'gemini-4-neon-xhigh'}},
        step(0, 'user_input', 'DONE'), step(1, 'model_response', 'DONE'),
        {'event': 'result', 'result': {'conversation_id': cid, 'status': 'SUCCESS', 'response': 'neon xhigh 完了'}},
    ]
    wire = ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events).encode('utf-8')
    assert extract_response('agy-stream-json', io.BytesIO(wire)) == 'neon xhigh 完了'.encode('utf-8')


def _agy_wire(steps, response, cid='conv-1'):
    events = [{'event': 'init', 'conversation_id': cid, 'init': {}}]
    for index, kind, state, *text in steps:
        step = {'conversation_id': cid, 'step_index': index, 'state': state, 'step_type': kind}
        if text:
            step['text_delta'] = text[0]
        events.append({'event': 'step_update', 'step_update': step})
    events.append({'event': 'result', 'result': {'conversation_id': cid, 'status': 'SUCCESS', 'response': response}})
    return ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events).encode('utf-8')


AGY_NARRATED_STEPS = [(0, 'user_input', 'DONE'), (1, 'agent_response', 'DONE', 'Waiting for tests.\n'),
                      (2, 'tool', 'ERROR'), (3, 'agent_response', 'ACTIVE', '{"a":'),
                      (3, 'agent_response', 'DONE', '1}')]


def test_agy_capture_uses_final_assistant_message_and_tolerates_tool_errors():
    import io
    from ai_skills.provider_capture import extract_response
    wire = _agy_wire(AGY_NARRATED_STEPS, 'Waiting for tests.\n{"a":1}')
    assert extract_response('agy-stream-json', io.BytesIO(wire)) == b'{"a":1}'


@pytest.mark.parametrize(('steps', 'response'), [
    # A later non-message step means the final message is not the turn's end.
    (AGY_NARRATED_STEPS + [(4, 'tool', 'DONE')], 'Waiting for tests.\n{"a":1}'),
    # The final message must be what result.response ends with.
    (AGY_NARRATED_STEPS, 'Waiting for tests.\n{"a":2}'),
    # No assistant message text: keep result.response.
    ([(0, 'user_input', 'DONE'), (1, 'tool', 'DONE')], 'Waiting for tests.\n{"a":1}'),
])
def test_agy_capture_falls_back_to_full_response(steps, response):
    import io
    from ai_skills.provider_capture import extract_response
    assert extract_response('agy-stream-json', io.BytesIO(_agy_wire(steps, response))) == response.encode('utf-8')


@pytest.mark.parametrize('steps', [
    [(0, 'user_input', 'ERROR')],
    [(0, 'user_input', 'DONE'), (1, 'tool', 'CANCELLED')],
])
def test_agy_capture_rejects_failed_user_turn_and_unknown_states(steps):
    import io
    from ai_skills.provider_capture import extract_response
    with pytest.raises(ValueError):
        extract_response('agy-stream-json', io.BytesIO(_agy_wire(steps, '{}')))


def test_agy_capture_rejects_non_string_text_delta():
    import io
    from ai_skills.provider_capture import extract_response
    wire = _agy_wire([(0, 'user_input', 'DONE'), (1, 'agent_response', 'DONE', 7)], '{}')
    with pytest.raises(ValueError, match='text delta'):
        extract_response('agy-stream-json', io.BytesIO(wire))


CARBON = 'gemini-4-carbon-high'
STREAM_FLAGS = ['--input-format', 'stream-json', '--output-format', 'stream-json']
AGY_FORMAT_HELP = {
    '--input-format': 'Input format for print mode (text, stream-json) (default text)',
    '--output-format': 'Output format for print mode (text, json, stream-json) (default text)',
}
# The unknown-model and authentication strings follow the AGY headless error
# documentation. The Carbon account-access denial is a synthetic fixture.
UNKNOWN_MODEL = (f'invalid model selection (--model "{CARBON}" --effort ""): model {CARBON} '
                 'is not recognized as a known model or custom model in settings\nAvailable models:\n')
AGY_FAILURES = {
    'unknown-model': ('model-unsupported', 'administrator', {'status': 'ERROR', 'response': '', 'error': UNKNOWN_MODEL}),
    'carbon-denied': ('model-unsupported', 'administrator',
                      {'status': 'ERROR', 'error': f'permission denied: this account does not have access to model {CARBON}'}),
    'named-model-denied': ('model-unsupported', 'administrator',
                           {'status': 'ERROR', 'error': f'Access denied for "{CARBON}"'}),
    'auth': ('auth', 'authenticate', {'status': 'ERROR', 'response': '', 'error': 'authentication required'}),
    'quota': ('quota', 'quota resets', {'status': 'ERROR', 'error': 'RESOURCE_EXHAUSTED: usage quota exceeded'}),
}
STDERR_AUTH = 'authentication required'
AGY_MIXED_FAILURES = {
    'generic-with-auth-stderr': ('auth', 'authenticate', {'status': 'ERROR', 'error': 'request failed'}),
    'quota-with-auth-stderr': AGY_FAILURES['quota'],
}


def explicit_agy_cli(tmp_path, monkeypatch, response="raise AssertionError('inference must not start')", *,
                     help_text=None, command=None):
    from ai_skills import cli

    if help_text is None:
        help_text = '\n'.join(['--model', *(f'{flag} {description}' for flag, description in AGY_FORMAT_HELP.items())])
    config = configuration(tmp_path, 'agy', response=response, help_text=help_text)
    if command is not None:
        registry = json.loads(config.registry_file.read_bytes())
        invocation = registry['agents']['agy']['invocation']
        invocation['command'] = str(command)
        invocation['args_template'].pop(0)
        config.registry_file.write_text(json.dumps(registry), encoding='utf-8')
    for key in list(os.environ):
        if key.startswith('RUN_WITH_IT_') or key in ('AGENT', 'MODEL', 'MODEL_EFFORT', 'AGENT_EXTRA_ARGS', 'UNATTENDED'):
            monkeypatch.delenv(key)
    (tmp_path / 'assets').mkdir()
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('RUN_WITH_IT_HEARTBEAT_SECONDS', '0')
    for name in ('log_file', 'events_log', 'done_file', 'status_file'):
        monkeypatch.setenv('RUN_WITH_IT_' + name.upper(), str(tmp_path / name))
    return cli.main(['agent', 'run', '--agent', 'agy', '--model', CARBON, '--repo-root', str(tmp_path),
                     '--registry-file', str(config.registry_file), '--context-file', str(config.context_file),
                     '--prompt-file', str(config.prompt_file), '--gui-mode', '0', '--unattended',
                     '--process-state-file', str(tmp_path / 'state.json')])


def status_lines(tmp_path, kind):
    return [line for line in (tmp_path / 'events_log').read_text(encoding='utf-8').splitlines()
            if line.startswith(f'STATUS|type={kind}|')]


def calls(tmp_path):
    path = tmp_path / 'calls.log'
    return path.read_text(encoding='utf-8').split() if path.exists() else []


def assert_failed_without_success(tmp_path, output, code, reason=None):
    assert code != 0
    assert output.out == ''
    assert all('status=failed' in line for line in status_lines(tmp_path, 'agent-complete'))
    assert len(status_lines(tmp_path, 'agent-complete')) == 1
    assert 'status=failed' in (tmp_path / 'done_file').read_text(encoding='utf-8')
    unavailable = status_lines(tmp_path, 'agent-unavailable')
    if reason is None:
        assert unavailable == []
    else:
        assert len(unavailable) == 1
        assert f'|model={CARBON}|' in unavailable[0] + '|'
        assert f'|reason={reason}|' in unavailable[0] + '|'
        assert '|action=exclude-route' in unavailable[0]


@pytest.mark.parametrize('case', ['missing', '--model', '--input-format', '--output-format'])
def test_explicit_agy_preflight_reports_model_and_remedy(tmp_path, monkeypatch, capsys, case):
    flags = [flag for flag in ('--model', '--input-format', '--output-format') if flag != case]
    command = tmp_path / 'missing-agy' if case == 'missing' else None
    code = explicit_agy_cli(tmp_path, monkeypatch, help_text='\n'.join(flags), command=command)
    output = capsys.readouterr()
    reason = 'missing-command' if case == 'missing' else 'cli-unsupported'
    assert code == (127 if case == 'missing' else 1)
    assert_failed_without_success(tmp_path, output, code, reason)
    remedy = ('install', 'PATH') if case == 'missing' else ('Upgrade', case)
    guidance = [line for line in output.err.splitlines()
                if not line.startswith('STATUS|') and CARBON in line and all(word in line for word in remedy)]
    assert guidance and guidance[0] in (tmp_path / 'log_file').read_text(encoding='utf-8')
    assert 'run' not in calls(tmp_path)
    assert not (tmp_path / 'received.bin').exists()


@pytest.mark.parametrize(('flag', 'description'), [
    ('--input-format', 'Input format for print mode (text). Older builds lack stream-json support.'),
    ('--output-format', 'Output format for print mode (text, json) (default text)'),
])
def test_explicit_agy_preflight_rejects_incompatible_advertised_format(tmp_path, monkeypatch, capsys, flag, description):
    formats = AGY_FORMAT_HELP | {flag: description}
    help_text = '\n'.join(['--model', *(f'{name} {details}' for name, details in formats.items())])
    code = explicit_agy_cli(tmp_path, monkeypatch, help_text=help_text)
    output = capsys.readouterr()
    assert code == 1
    assert_failed_without_success(tmp_path, output, code, 'cli-unsupported')
    assert any(CARBON in line and 'Upgrade' in line and flag in line for line in output.err.splitlines())
    assert calls(tmp_path) == ['probe']
    assert not (tmp_path / 'received.bin').exists()


def agy_failure_script(result, code, stderr=''):
    return (f"print({stderr!r}, file=sys.stderr)\n"
            f"print(json.dumps({{'event': 'result', 'result': {result!r}}}))\nraise SystemExit({code})")


def assert_one_exact_inference(tmp_path):
    assert calls(tmp_path) == ['probe', 'run']
    argv = json.loads((tmp_path / 'argv.json').read_text(encoding='utf-8'))
    assert argv.count('--model') == 1 and argv[argv.index('--model') + 1] == CARBON
    assert argv[-4:] == STREAM_FLAGS


@pytest.mark.parametrize('case', sorted(AGY_FAILURES))
@pytest.mark.parametrize('exit_code', [0, 1])
def test_explicit_agy_failure_reports_requested_model_and_remedy(tmp_path, monkeypatch, capsys, case, exit_code):
    reason, remedy, result = AGY_FAILURES[case]
    code = explicit_agy_cli(tmp_path, monkeypatch, agy_failure_script(result, exit_code))
    output = capsys.readouterr()
    log_path = tmp_path / 'log_file'
    assert code == (exit_code or 1)
    assert_failed_without_success(tmp_path, output, code, reason)
    assert_one_exact_inference(tmp_path)
    guidance = [line for line in output.err.splitlines()
                if not line.startswith('STATUS|') and CARBON in line and remedy in line.lower()]
    assert guidance and guidance[0] in log_path.read_text(encoding='utf-8')
    assert log_signals_unavailable_route(str(log_path))
    if reason == 'model-unsupported':
        assert all(phrase in guidance[0] for phrase in ('agy models', 'signed-in account', 'if the model is absent'))
    assert json.loads((tmp_path / 'state.json').read_bytes())['return_code'] == code


@pytest.mark.parametrize('case', AGY_MIXED_FAILURES)
def test_explicit_agy_terminal_and_stderr_precedence(tmp_path, monkeypatch, capsys, case):
    reason, remedy, result = AGY_MIXED_FAILURES[case]
    code = explicit_agy_cli(tmp_path, monkeypatch, agy_failure_script(result, 8, STDERR_AUTH))
    output = capsys.readouterr()
    assert code == 8
    assert_failed_without_success(tmp_path, output, code, reason)
    assert_one_exact_inference(tmp_path)
    assert any(CARBON in line and remedy in line for line in output.err.splitlines())


def test_explicit_agy_stderr_only_startup_failure_reports_model_and_remedy(tmp_path, monkeypatch, capsys):
    script = "print('Error: authentication required; run agy to sign in', file=sys.stderr)\nraise SystemExit(1)"
    code = explicit_agy_cli(tmp_path, monkeypatch, script)
    output = capsys.readouterr()
    assert code == 1
    assert_failed_without_success(tmp_path, output, code, 'auth')
    assert_one_exact_inference(tmp_path)
    assert any(CARBON in line and 'authenticate' in line.lower() and not line.startswith('STATUS|')
               for line in output.err.splitlines())


def test_explicit_agy_retains_stderr_when_provider_exits_without_terminal(tmp_path, monkeypatch, capsys):
    diagnostic = 'FATAL: network is unreachable'
    script = f"print({diagnostic!r}, file=sys.stderr)\nraise SystemExit(3)"
    code = explicit_agy_cli(tmp_path, monkeypatch, script)
    output = capsys.readouterr()
    assert code == 3
    assert_failed_without_success(tmp_path, output, code)
    assert any(CARBON in line and 'exit code 3' in line and 'without a terminal result' in line
               for line in output.err.splitlines())
    assert 'malformed' not in output.err.lower()
    _, separator, path = output.err.partition('AGY stderr saved to ')
    assert separator
    assert diagnostic in Path(path.splitlines()[0]).read_text(encoding='utf-8')
    log_file = tmp_path / 'log_file'
    assert diagnostic not in log_file.read_text(encoding='utf-8')
    assert not log_signals_unavailable_route(str(log_file))


def test_explicit_agy_reports_nonzero_exit_after_success_terminal(tmp_path, monkeypatch, capsys):
    result = {'status': 'SUCCESS', 'response': 'This response must not be published.'}
    script = agy_failure_script(result, 2, 'segfault in renderer')
    code = explicit_agy_cli(tmp_path, monkeypatch, script)
    output = capsys.readouterr()
    assert code == 2
    assert_failed_without_success(tmp_path, output, code)
    assert any('AGY failed' in line and CARBON in line and 'exit code 2' in line
               for line in output.err.splitlines())


QUOTA_STEP = {'event': 'step_update', 'step_update': {'text_delta': 'tool said: usage limit and quota exceeded'}}
MALFORMED_AGY = {
    'invalid-json': "print(json.dumps(step)); print('{not json')",
    'no-terminal': 'print(json.dumps(step))',
    'duplicate-terminal': "print(json.dumps(step)); [print(json.dumps(done)) for _ in range(2)]",
    'null-then-terminal': "print(json.dumps(step)); print(json.dumps({'event': 'result', 'result': None})); print(json.dumps(done))",
    'missing-response': "print(json.dumps(step)); print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS'}}))",
    'non-string-response': "print(json.dumps(step)); print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': 5}}))",
    'missing-status': "print(json.dumps(step)); print(json.dumps({'event': 'result', 'result': {'response': 'ok'}}))",
    'empty-non-string-error': "done['result']['error'] = []; print(json.dumps(done))",
    'invalid-status-type': "done['result'].update(status=False, error='quota exceeded'); print(json.dumps(done))",
    'invalid-status-value': "done['result'].update(status='UNKNOWN', error='quota exceeded'); print(json.dumps(done))",
    'invalid-error-type': "done['result'].update(status='ERROR', error={'detail': 'quota exceeded'}); print(json.dumps(done))",
    'non-object-record': "print('false'); print(json.dumps(done))",
    'record-after-terminal': 'print(json.dumps(done)); print(json.dumps(step))',
    'error-and-success': ("print(json.dumps(step)); print(json.dumps({'event': 'result', 'result': "
                          "{'status': 'SUCCESS', 'response': 'ok', 'error': 'boom'}}))"),
}


@pytest.mark.parametrize('case', sorted(MALFORMED_AGY))
@pytest.mark.parametrize('exit_code', [0, 3])
def test_explicit_agy_validates_terminal_output(tmp_path, monkeypatch, capsys, case, exit_code):
    script = (f"step = {QUOTA_STEP!r}\ndone = {{'event': 'result', 'result': {{'status': 'SUCCESS', 'response': 'ok'}}}}\n"
              + MALFORMED_AGY[case] + f'\nraise SystemExit({exit_code})')
    code = explicit_agy_cli(tmp_path, monkeypatch, script)
    output = capsys.readouterr()
    assert code == (exit_code or 1)
    assert_failed_without_success(tmp_path, output, code)
    assert_one_exact_inference(tmp_path)
    if exit_code == 0:
        assert any(CARBON in line and 'malformed' in line.lower() for line in output.err.splitlines())


def test_explicit_agy_rejects_invalid_unicode_before_publishing(tmp_path, monkeypatch, capsys):
    result = {'status': 'SUCCESS', 'response': 'valid first line\nlone surrogate: \ud800\n'}
    code = explicit_agy_cli(tmp_path, monkeypatch, agy_failure_script(result, 0))
    output = capsys.readouterr()
    assert_failed_without_success(tmp_path, output, code)
    assert any(CARBON in line and 'malformed' in line.lower() for line in output.err.splitlines())
    state = json.loads((tmp_path / 'state.json').read_bytes())
    assert (state['state'], state['return_code']) == ('dead', code)


@pytest.mark.parametrize('empty_error', [{}, {'error': None}, {'error': ''}], ids=['omitted', 'null', 'empty-string'])
def test_explicit_agy_valid_result_with_quota_phrase_succeeds(tmp_path, monkeypatch, capsys, empty_error):
    narrative = 'quota 🙂 usage limit 完了\n'
    script = (f"print(json.dumps({QUOTA_STEP!r}))\n"
              f"print(json.dumps({{'event': 'result', 'result': {{'status': 'SUCCESS', 'response': {narrative!r}, **{empty_error!r}}}}}, ensure_ascii=False))")
    code = explicit_agy_cli(tmp_path, monkeypatch, script)
    output = capsys.readouterr()
    assert code == 0
    assert output.out == narrative
    assert status_lines(tmp_path, 'agent-unavailable') == []
    assert 'status=success' in (tmp_path / 'done_file').read_text(encoding='utf-8')
    assert_one_exact_inference(tmp_path)


@pytest.mark.parametrize(('case', 'exit_code', 'stderr'), [
    *((case, code, '') for case in sorted(AGY_FAILURES) for code in (0, 2)),
    *((case, 8, STDERR_AUTH) for case in AGY_MIXED_FAILURES),
])
def test_agy_stream_capture_preserves_explicit_failure(tmp_path, capsys, case, exit_code, stderr):
    reason, remedy, result = (AGY_FAILURES | AGY_MIXED_FAILURES)[case]
    cid = 'conv-carbon'
    events = [
        {'event': 'init', 'conversation_id': cid, 'init': {'model': CARBON}},
        {'event': 'step_update', 'step_update': {'conversation_id': cid, 'step_index': 0,
                                                 'step_type': 'user_input', 'state': 'DONE', 'text_delta': 'quota'}},
        {'event': 'result', 'result': {**result, 'conversation_id': cid}},
    ]
    script = (f'print({stderr!r}, file=sys.stderr)\nfor event in {events!r}: print(json.dumps(event))\n'
              f'raise SystemExit({exit_code})')
    config = configuration(tmp_path, 'agy', response=script)
    config = config.replace(model=CARBON, role='impl', dispatcher_managed=True, state_file=tmp_path / 'worker.json',
                            result_file=tmp_path / 'result.json', result_capture_context={'source': 'agy-stream-json'})
    outcome = run_agent(config)
    assert capsys.readouterr().out == ''
    assert (outcome.return_code, outcome.unavailable_reason, outcome.model) == (exit_code or 1, reason, CARBON)
    state = json.loads(config.process_state_file.read_bytes())
    assert (state['state'], state['return_code'], state['unavailable_reason']) == ('dead', exit_code or 1, reason)
    unavailable = [line for line in config.events_log.read_text(encoding='utf-8').splitlines()
                   if line.startswith('STATUS|type=agent-unavailable|')]
    assert len(unavailable) == 1 and f'model={CARBON}' in unavailable[0] and f'reason={reason}' in unavailable[0]
    assert f'return_code={exit_code or 1}' in config.events_log.read_text(encoding='utf-8')
    assert [json.loads(line) for line in (tmp_path / 'streams' / 'stdout').read_text(encoding='utf-8').splitlines()] == events
    assert not config.result_file.exists()
    log = config.log_file.read_text(encoding='utf-8')
    assert result['error'].splitlines()[0] not in log
    assert any(CARBON in line and remedy in line and 'STATUS|' not in line for line in log.splitlines())


@pytest.mark.parametrize('where', ['terminal-error', 'stderr', 'model-step'])
def test_agy_quota_records_reset_only_from_provider_text(tmp_path, capsys, where):
    cid = 'conv-quota'
    reset = '; resets 9:00pm (UTC)'
    error = 'RESOURCE_EXHAUSTED: usage quota exceeded' + (reset if where == 'terminal-error' else '')
    events = [{'event': 'init', 'conversation_id': cid, 'init': {'model': CARBON}}]
    if where == 'model-step':
        events.append({'event': 'step_update', 'step_update': {'conversation_id': cid, 'step_index': 0,
                       'step_type': 'user_input', 'state': 'DONE', 'text_delta': 'quota' + reset}})
    events.append({'event': 'result', 'result': {'status': 'ERROR', 'error': error, 'conversation_id': cid}})
    stderr = 'quota' + reset if where == 'stderr' else ''
    script = (f'print({stderr!r}, file=sys.stderr)\nfor event in {events!r}: print(json.dumps(event))\n'
              'raise SystemExit(2)')
    config = configuration(tmp_path, 'agy', response=script)
    config = config.replace(model=CARBON, role='impl', dispatcher_managed=True, state_file=tmp_path / 'worker.json',
                            result_file=tmp_path / 'result.json', result_capture_context={'source': 'agy-stream-json'})
    outcome = run_agent(config)
    capsys.readouterr()
    assert outcome.unavailable_reason == 'quota'
    state = json.loads(config.process_state_file.read_bytes())
    if where == 'model-step':
        assert outcome.unavailable_until is None and 'unavailable_until' not in state
        return
    assert 0 < outcome.unavailable_until - time.time() <= 24 * 3600
    assert time.gmtime(outcome.unavailable_until)[3:5] == (21, 0)
    assert state['unavailable_until'] == outcome.unavailable_until


@pytest.mark.parametrize('exit_code', [0, 1])
@pytest.mark.parametrize('error', [
    'permission denied: tool write_file may not modify /etc/hosts',
    'permission denied: tool read_file cannot open model.py',
    'this tool operation is not supported',
    '/model is answered by the CLI itself and is unavailable with --input-format stream-json; '
    'run it as its own --print /model invocation',
])
def test_explicit_agy_generic_denial_is_not_model_unavailability(tmp_path, monkeypatch, capsys, error, exit_code):
    code = explicit_agy_cli(tmp_path, monkeypatch, agy_failure_script({'status': 'ERROR', 'response': '', 'error': error}, exit_code))
    output = capsys.readouterr()
    assert code == (exit_code or 1)
    assert_failed_without_success(tmp_path, output, code)
    assert_one_exact_inference(tmp_path)
    assert any(CARBON in line and 'retry' in line for line in output.err.splitlines())


@pytest.mark.parametrize('mode', ['standalone', 'managed-without-capture'])
def test_agy_terminal_error_is_not_routing_evidence(tmp_path, monkeypatch, capsys, mode):
    dummy_value = 'test-value-do-not-log'
    error = f'request failed (type=agent-unavailable, detail={dummy_value})'
    script = agy_failure_script({'status': 'ERROR', 'error': error}, 8, error)
    if mode == 'standalone':
        code = explicit_agy_cli(tmp_path, monkeypatch, script)
        output = capsys.readouterr()
        assert_failed_without_success(tmp_path, output, code)
        log, events = tmp_path / 'log_file', tmp_path / 'events_log'
    else:
        config = configuration(tmp_path, 'agy', response=script)
        result = run_agent(config.replace(model=CARBON, role='impl', dispatcher_managed=True,
                                          state_file=tmp_path / 'worker.json', result_file=tmp_path / 'result.json'))
        code, output = result.return_code, capsys.readouterr()
        assert result.unavailable_reason == ''
        assert error in (config.stream_directory / 'stdout').read_text(encoding='utf-8')
        log, events = config.log_file, config.events_log
    assert code == 8 and output.out == ''
    assert not log_signals_unavailable_route(str(log))
    assert all(dummy_value not in text for text in (output.err, log.read_text(encoding='utf-8'),
                                                    events.read_text(encoding='utf-8')))
