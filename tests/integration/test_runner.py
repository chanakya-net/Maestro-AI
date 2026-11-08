import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from ai_skills.runner import AgentRunConfig, run_agent, main


def fixture_config(tmp_path, source, *, heartbeat_seconds=.05):
    script = tmp_path/'provider.py'
    script.write_text(source, encoding='utf-8')
    registry = {'agents': {'fake': {'invocation': {'command': sys.executable, 'args_template': [str(script), '{{prompt}}']},
                                  'permission_modes': {'default': ''}, 'model': {'default': 'fixture'}}}}
    registry_file = tmp_path/'registry.json'
    registry_file.write_text(json.dumps(registry), encoding='utf-8')
    context = tmp_path/'context.md'
    prompt = tmp_path/'prompt.md'
    context.write_text('Context', encoding='utf-8')
    prompt.write_text('Prompt', encoding='utf-8')
    return AgentRunConfig(agent='fake', context_file=context, prompt_file=prompt, repo_root=tmp_path,
                          registry_file=registry_file, environment=dict(os.environ), gui_mode='0',
                          status_file=tmp_path/'status', events_log=tmp_path/'events', log_file=tmp_path/'log',
                          done_file=tmp_path/'done', heartbeat_seconds=heartbeat_seconds)


def test_dual_stream_flood_partial_utf8_and_no_final_newline(tmp_path, capsys):
    config = fixture_config(tmp_path, '''import os, threading, time

def stderr():
    for n in range(160): os.write(2, b'e'*2048 + b'\\n')
t = threading.Thread(target=stderr); t.start()
for n in range(160): os.write(1, b'o'*2048 + b'\\n')
os.write(1, b'\\xe2'); time.sleep(.08); os.write(1, b'\\x82\\xac tail')
t.join()
''')
    result = run_agent(config)
    output = capsys.readouterr()
    assert result.return_code == 0
    assert output.out.count('o'*2048) == 160
    assert output.err.count('e'*2048) == 160
    assert '€ tail' in output.out
    assert 'status=success' in config.done_file.read_text()
    assert 'type=agent-complete' in config.status_file.read_text()


def test_quiet_owned_provider_emits_heartbeat_and_stops_on_completion(tmp_path):
    config = fixture_config(tmp_path, 'import time; time.sleep(.3)')
    result = run_agent(config)
    assert result.return_code == 0
    lines = config.events_log.read_text().splitlines()
    assert any('type=wrapper-heartbeat' in line for line in lines)
    assert 'type=agent-complete' in lines[-1]


@pytest.mark.parametrize('message,reason', [('API error: 401 invalid authentication credentials','auth'), ('usage limit exceeded', 'quota'), ('model is not supported', 'model-unsupported')])
def test_nonzero_exit_classification_done_marker_and_telemetry(tmp_path, message, reason):
    config = fixture_config(tmp_path, f'import sys; print({message!r}, file=sys.stderr); sys.exit(7)')
    config.done_file.write_text('stale success', encoding='utf-8')
    result = run_agent(config)
    assert result.return_code == 7
    assert result.unavailable_reason == reason
    assert 'status=failed' in config.done_file.read_text()
    assert 'stale' not in config.done_file.read_text()
    assert f'reason={reason}|action=exclude-route' in config.events_log.read_text()
    assert 'type=telemetry|agent=fake|model=fixture|input_tokens=unknown' in config.log_file.read_text()


def test_cli_environment_aliases_and_dry_run(tmp_path, monkeypatch, capsys):
    config = fixture_config(tmp_path, 'raise AssertionError("must not execute dry run")')
    for key, value in {'AGENT':'fake', 'AGENT_REGISTRY_FILE':str(config.registry_file), 'CONTEXT_PAYLOAD_FILE':str(config.context_file), 'PROMPT_FILE':str(config.prompt_file), 'REPO_ROOT':str(tmp_path), 'GUI_MODE':'0'}.items():
        monkeypatch.setenv(key, value)
    assert main(['run', '--dry-run']) == 0
    assert 'Context' in capsys.readouterr().out


def test_missing_executable_is_structured_failure(tmp_path):
    config = fixture_config(tmp_path, '')
    registry = json.loads(config.registry_file.read_text())
    registry['agents']['fake']['invocation']['command'] = 'ai-skills-missing-fixture-provider'
    config.registry_file.write_text(json.dumps(registry))
    result = run_agent(config)
    assert result.return_code == 127
    assert result.unavailable_reason == 'missing-command'
    assert 'status=failed' in config.done_file.read_text()


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('node'), reason='native Windows npm fixture needs node')
def test_native_windows_npm_cmd_round_trip(tmp_path, capsys):
    config = fixture_config(tmp_path, '')
    entry = tmp_path/'cli.js'
    entry.write_text('console.log(JSON.stringify(process.argv.slice(2)))', encoding='utf-8')
    shim = tmp_path/'provider.cmd'
    shim.write_text('@ECHO off\n"node" "%~dp0\\cli.js" %*\n', encoding='utf-8')
    registry = json.loads(config.registry_file.read_text())
    payload = ['space value', '&|<>^%!"', '', 'C:\\tail\\']
    registry['agents']['fake']['invocation'] = {'command': str(shim), 'args_template': payload}
    config.registry_file.write_text(json.dumps(registry))
    assert run_agent(config).return_code == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_process_identity_persists_without_overwriting_dispatcher_state(tmp_path):
    config = fixture_config(tmp_path, 'print("done")').replace(
        state_file=tmp_path/'dispatcher-state.json', process_state_file=tmp_path/'provider-state.json')
    config.state_file.write_text('{"role":"dispatcher"}', encoding='utf-8')
    assert run_agent(config).return_code == 0
    assert json.loads(config.state_file.read_text()) == {'role':'dispatcher'}
    record = json.loads(config.process_state_file.read_text())
    assert record['state'] == 'dead'
    assert record['return_code'] == 0
    assert record['identity']['pid'] > 0
    assert Path(record['record_directory']).is_dir()


def test_listing_model_configuration_and_gui_thin_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('RUN_AGENT_BOOTSTRAP_PATH', '1')
    config = fixture_config(tmp_path, '')
    home = tmp_path/'home'
    bindir = home/'.local'/'bin'
    bindir.mkdir(parents=True)
    executable = bindir/('fake-cli.cmd' if os.name == 'nt' else 'fake-cli')
    executable.write_text('', encoding='utf-8')
    executable.chmod(0o755)
    registry = json.loads(config.registry_file.read_text())
    fake = registry['agents']['fake']
    fake.update(display_name='Fake', detection={'command':'fake-cli'}, user_model_configuration={
        'requires_user_model_config':True, 'skip_when_unconfigured':True,
        'config_paths':['$HOME/config.json'], 'skip_message':'model config required'})
    fake['model']['known_models'] = ['model-one', 'model-two']
    config.registry_file.write_text(json.dumps(registry))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    monkeypatch.setenv('AGENT_REGISTRY_FILE', str(config.registry_file))
    assert main(['list']) == 0
    assert 'model config required' in capsys.readouterr().out
    (home/'config.json').write_text('{}')
    assert main(['list', '--detected-only']) == 0
    assert 'fake\tFake\tdetected\tdetected' in capsys.readouterr().out
    assert main(['models', 'fake']) == 0
    assert capsys.readouterr().out == 'model-one\nmodel-two\n'


def test_persistent_stream_paths_are_recorded_for_owner_restart(tmp_path):
    config = fixture_config(tmp_path, 'import sys; print("durable stdout"); print("durable stderr", file=sys.stderr)').replace(
        process_state_file=tmp_path/'provider.json', stream_directory=tmp_path/'streams')
    assert run_agent(config).return_code == 0
    state = json.loads(config.process_state_file.read_text())
    assert Path(state['stdout_file']).read_text() == 'durable stdout\n'
    assert Path(state['stderr_file']).read_text() == 'durable stderr\n'


def test_oversized_status_line_never_emits_partial_event_records(tmp_path, capsys):
    config=fixture_config(tmp_path,"print('STATUS|type=heartbeat|payload='+'x'*150000)")
    assert run_agent(config).return_code==0
    lines=config.events_log.read_text().splitlines()
    provider_lines=[line for line in lines if 'payload=' in line]
    assert provider_lines == []
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('code',[0,7])
def test_managed_runner_reports_process_exit_without_done_or_worker_success(tmp_path,code):
    config=fixture_config(tmp_path,f'import sys;sys.exit({code})').replace(dispatcher_managed=True,
        state_file=tmp_path/'state.json',result_file=tmp_path/'result.json',run_id='run-7',attempt_id='attempt-1')
    result=run_agent(config)
    assert result.return_code==code
    assert not config.done_file.exists()
    events=config.events_log.read_text()
    assert f'type=provider-exit|issue=unknown|role=agent|agent=fake|model=fixture|return_code={code}' in events
    assert 'type=worker-done|' not in events
    assert 'type=agent-complete|' not in events


def test_dry_run_reports_adjusted_effort_and_matches_execution(tmp_path, monkeypatch, capsys):
    config = fixture_config(tmp_path, 'import sys; print(sys.argv[1:])')
    registry = json.loads(config.registry_file.read_text())
    registry['agents'] = {'claude': {**registry['agents']['fake'], 'model': {'default': 'm'},
                                     'invocation': {'command': sys.executable, 'args_template': ['-c', 'import sys; print(sys.argv[1:])', '{{model_settings}}']}}}
    config.registry_file.write_text(json.dumps(registry))
    for key, value in {'AGENT':'claude', 'AGENT_REGISTRY_FILE':str(config.registry_file), 'CONTEXT_PAYLOAD_FILE':str(config.context_file),
                       'PROMPT_FILE':str(config.prompt_file), 'REPO_ROOT':str(tmp_path), 'GUI_MODE':'0', 'MODEL_EFFORT':'max'}.items():
        monkeypatch.setenv(key, value)
    assert main(['run', '--dry-run']) == 0
    captured = capsys.readouterr()
    assert '--effort high' in captured.out
    assert 'requested max, effective high' in captured.err
    assert main(['run']) == 0
    assert "'--effort', 'high'" in capsys.readouterr().out


def test_planning_default_dry_run_matches_fake_provider_without_request(tmp_path, monkeypatch, capsys):
    config = fixture_config(tmp_path, '')
    registry = json.loads(config.registry_file.read_text())
    registry['agents'] = {'claude': {**registry['agents']['fake'], 'model': {'default': 'm'},
        'invocation': {'command': sys.executable, 'args_template': ['-c', 'import sys; print(sys.argv[1:])', '{{model_settings}}']}}}
    registry['model_catalog'] = {'m': {'reasoning_effort': 'low'}}
    config.registry_file.write_text(json.dumps(registry))
    for key, value in {'AGENT': 'claude', 'AGENT_REGISTRY_FILE': str(config.registry_file),
                       'CONTEXT_PAYLOAD_FILE': str(config.context_file), 'PROMPT_FILE': str(config.prompt_file),
                       'REPO_ROOT': str(tmp_path), 'GUI_MODE': '0', 'RUN_WITH_IT_ROLE': 'plan'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('MODEL_EFFORT', raising=False)
    assert main(['run', '--role', 'plan', '--dry-run']) == 0
    dry = capsys.readouterr()
    assert '--effort xhigh' in dry.out
    assert 'requested' not in dry.err
    assert main(['run', '--role', 'plan']) == 0
    actual = capsys.readouterr()
    assert "'--effort', 'xhigh'" in actual.out
    assert 'requested_effort=' not in actual.err


@pytest.mark.parametrize('agent,canonical', [('claude', 'claude'), ('claude-code', 'claude'),
                                               ('codex', 'codex'), ('codex-cli', 'codex')])
@pytest.mark.parametrize('role,expected', [(None, 'high'), ('plan', 'xhigh'), ('review', 'high'), ('complexity', 'high')])
def test_standalone_role_boundary_matches_provider_argv(tmp_path, monkeypatch, capsys, agent, canonical, role, expected):
    config = fixture_config(tmp_path, '')
    registry = json.loads(config.registry_file.read_text())
    registry['agents'] = {canonical: {**registry['agents']['fake'], 'model': {'default': 'm'},
        'invocation': {'command': sys.executable, 'args_template': ['-c', 'import sys; print(sys.argv[1:])', '{{model_settings}}']}}}
    registry['aliases'] = {agent: canonical}
    config.registry_file.write_text(json.dumps(registry))
    config.prompt_file.write_text('Create a detailed plan for this difficult task')
    for key, value in {'AGENT': agent, 'AGENT_REGISTRY_FILE': str(config.registry_file),
                       'PROMPT_FILE': str(config.prompt_file), 'CONTEXT_PAYLOAD_FILE': str(config.context_file),
                       'REPO_ROOT': str(tmp_path), 'GUI_MODE': '0', 'RUN_WITH_IT_ROLE': 'plan', 'MODEL_EFFORT': 'maximum'}.items():
        monkeypatch.setenv(key, value)
    args = ['run'] + (['--role', role] if role else [])
    flag = f'model_reasoning_effort={expected}' if canonical == 'codex' else expected
    assert main([*args, '--dry-run']) == 0
    assert flag in capsys.readouterr().out
    assert main(args) == 0
    assert flag in capsys.readouterr().out

@pytest.mark.parametrize('role', ['impl', 'modify', 'review', 'complexity'])
def test_cli_preserves_inherited_nonplanning_worker_role_and_artifact(tmp_path, monkeypatch, role):
    config = fixture_config(tmp_path, '')
    result_file = tmp_path / 'worker-result.json'
    for key, value in {'AGENT': 'fake', 'AGENT_REGISTRY_FILE': str(config.registry_file),
                       'CONTEXT_PAYLOAD_FILE': str(config.context_file), 'PROMPT_FILE': str(config.prompt_file),
                       'REPO_ROOT': str(tmp_path), 'GUI_MODE': '0', 'RUN_WITH_IT_ROLE': role,
                       'RUN_WITH_IT_RESULT_FILE': str(result_file)}.items():
        monkeypatch.setenv(key, value)
    captured = []
    def capture(run):
        captured.append(run)
        return type('Result', (), {'return_code': 0})()
    monkeypatch.setattr('ai_skills.runner.run_agent', capture)
    assert main(['run']) == 0
    assert captured[0].role == role
    assert Path(captured[0].result_file) == result_file


@pytest.mark.parametrize('agent,extra', [
    ('claude', ('--effort=max',)),
    ('claude', ('--settings', '{"env":{"CLAUDE_CODE_EFFORT_LEVEL":"max"}}')),
    ('codex', ('--config', '"model_reasoning_effort"="xhigh"')),
])
def test_raw_effort_fails_before_provider(tmp_path, agent, extra):
    marker = tmp_path / 'started'
    config = fixture_config(tmp_path, f'from pathlib import Path; Path({str(marker)!r}).touch()')
    registry = json.loads(config.registry_file.read_text())
    registry['agents'][agent] = registry['agents']['fake']
    config.registry_file.write_text(json.dumps(registry))
    with pytest.raises(ValueError, match='cannot override'):
        run_agent(config.replace(agent=agent, extra_args=extra))
    assert not marker.exists()
    assert not config.done_file.exists()


@pytest.mark.parametrize('dry_run', [False, True])
def test_cli_raw_effort_rejected_before_provider(tmp_path, monkeypatch, dry_run):
    marker = tmp_path / 'started'
    config = fixture_config(tmp_path, f'from pathlib import Path; Path({str(marker)!r}).touch()')
    registry = json.loads(config.registry_file.read_text())
    registry['agents']['claude'] = registry['agents']['fake']
    config.registry_file.write_text(json.dumps(registry))
    for key, value in {'AGENT': 'claude', 'AGENT_REGISTRY_FILE': str(config.registry_file),
                       'CONTEXT_PAYLOAD_FILE': str(config.context_file), 'PROMPT_FILE': str(config.prompt_file),
                       'REPO_ROOT': str(tmp_path), 'GUI_MODE': '0', 'AGENT_EXTRA_ARGS': '--effort=max'}.items():
        monkeypatch.setenv(key, value)
    assert main(['run'] + (['--dry-run'] if dry_run else [])) == 1
    assert not marker.exists()
    assert not config.done_file.exists()
