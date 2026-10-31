"""Exercise real legacy-encoded process streams, outside pytest capture wrappers."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_skills.events import EventBus
from test_prompt_transport import configuration


def legacy_environment():
    return {**os.environ, 'PYTHONIOENCODING':'cp1252'}


@pytest.mark.parametrize('entrypoint', ['cli', 'library'])
def test_utf8_provider_output_survives_legacy_encoded_caller(tmp_path, entrypoint):
    config = configuration(tmp_path, response="sys.stdout.buffer.write('λ你好\\n'.encode('utf-8')); sys.stderr.buffer.write('診断🙂\\n'.encode('utf-8'))")
    options = ['--agent', 'codex', '--registry-file', str(config.registry_file),
               '--context-file', str(config.context_file), '--prompt-file', str(config.prompt_file),
               '--repo-root', str(tmp_path), '--unattended', '--gui-mode', '0']
    if entrypoint == 'cli':
        command = [sys.executable, '-m', 'ai_skills', 'agent', 'run', *options]
    else:
        source = '''import sys
from pathlib import Path
from ai_skills.runner import AgentRunConfig,run_agent
root=Path(sys.argv[1])
config=AgentRunConfig(agent='codex',registry_file=root/'registry.json',context_file=root/'context.md',prompt_file=root/'prompt.md',repo_root=root,unattended=True,gui_mode='0')
raise SystemExit(run_agent(config).return_code)
'''
        command = [sys.executable, '-c', source, str(tmp_path)]
    result = subprocess.run(command, env=legacy_environment(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert result.stdout == 'λ你好\n'.encode('utf-8')
    assert '診断🙂\n'.encode('utf-8') in result.stderr
    assert b'charmap' not in result.stderr


def test_cli_accepts_utf8_stdin_task_and_emits_utf8_machine_paths(tmp_path):
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'agents':{'fake':{
        'detection':{'command':sys.executable},
        'invocation':{'command':sys.executable,'args_template':['-c',"raise AssertionError('start must not invoke a provider')"]},
        'model':{'known_models':['one']},'permission_modes':{'read_only':'--read-only'}}}}), encoding='utf-8')
    command = [sys.executable, '-m', 'ai_skills', 'pair', 'start', '-', '--run-root', str(tmp_path/'讨论')]
    for slot in ('1','2'):
        command.extend([f'--agent-{slot}-harness','fake',f'--agent-{slot}-model','one',f'--agent-{slot}-effort','high'])
    task = '设计持久存储 λ🙂\n'
    env = {**legacy_environment(), 'AGENT_REGISTRY_FILE':str(registry)}
    result = subprocess.run(command, cwd=tmp_path, input=task.encode('utf-8'), env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    values = dict(line.split('=', 1) for line in result.stdout.decode('utf-8').splitlines() if '=' in line)
    assert '讨论' in values['RUN_DIR']
    assert (Path(values['RUN_DIR'])/'task.md').read_bytes() == task.encode('utf-8')


def test_explicit_cli_argv_preserves_library_caller_stream_encoding():
    source = '''import json,sys
from ai_skills.cli import main
before=[stream.encoding for stream in (sys.stdin,sys.stdout,sys.stderr)]
code=main(['--version'])
assert [stream.encoding for stream in (sys.stdin,sys.stdout,sys.stderr)]==before
raise SystemExit(code)
'''
    result = subprocess.run([sys.executable, '-c', source], env=legacy_environment(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_event_output_keeps_text_only_api_streams(monkeypatch):
    target = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', target)
    EventBus().forward('λ你好', 'stdout')
    assert target.getvalue() == 'λ你好\n'
