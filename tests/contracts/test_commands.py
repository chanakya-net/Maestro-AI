"""The installed command exposes working native workflow entry points."""
import subprocess
import sys
import json
import os

import pytest


@pytest.mark.parametrize('command', ['agent', 'state', 'route', 'artifact', 'github', 'report', 'pair'])
def test_native_command_help_is_reachable(command):
    result = subprocess.run([sys.executable, '-m', 'ai_skills', command, '--help'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()


def test_github_update_and_report_pr_are_real_subcommands(tmp_path):
    state = tmp_path / 'state.json'
    report = tmp_path / 'report.json'
    state.write_text(json.dumps({'issue_registry': {'42': {'status': 'completed'}}}), encoding='utf-8')
    report.write_text('{}', encoding='utf-8')
    env = {**os.environ, 'RUN_WITH_IT_GITHUB_UPDATES': '0'}
    result = subprocess.run([sys.executable, '-m', 'ai_skills', 'github', 'update', '--state-file', str(state), '--run-root', str(tmp_path), '--issue', '42', '--outcome', 'completed', '--report-file', str(report)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())['issue_registry']['42']['github_update_status'] == 'skipped'
    result = subprocess.run([sys.executable, '-m', 'ai_skills', 'report', 'pr', '--state-file', str(state)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '42' in result.stdout
