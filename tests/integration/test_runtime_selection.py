import json
import os
from pathlib import Path
import sys

import pytest

from ai_skills import cli


def test_existing_run_overrides_new_active_runtime(tmp_path, monkeypatch):
    active = tmp_path / 'assets'
    active.mkdir()
    new_python = tmp_path / 'new-python'
    new_python.touch()
    (active / 'runtime.json').write_text(json.dumps({'schema_version': 1, 'python': str(new_python)}))
    root = tmp_path / 'project'
    (root / '.run-with-it').mkdir(parents=True)
    (root / '.run-with-it/engine.json').write_text(json.dumps({'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': sys.executable}))
    monkeypatch.setenv('ASSETS_DEST', str(active))
    assert cli.runtime_command(['run', 'pool', '--run-root', str(root)]) is None
    result = cli.runtime_command(['agent', 'list'])
    assert result[:4] == [str(new_python), '-I', '-m', 'ai_skills']


def test_missing_pinned_interpreter_never_falls_back(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    (root / '.run-with-it').mkdir(parents=True)
    (root / '.run-with-it/engine.json').write_text(json.dumps({'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(tmp_path / 'missing-python')}))
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    with pytest.raises(ValueError, match='missing'):
        cli.runtime_command(['run', 'pool', '--run-root', str(root)])


def test_worker_config_and_inherited_run_root_select_same_runtime(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    (root / '.run-with-it').mkdir(parents=True)
    selected = tmp_path / 'pinned-python'
    selected.touch()
    (root / '.run-with-it/engine.json').write_text(json.dumps({'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(selected)}))
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    monkeypatch.setenv('AI_SKILLS_RUN_ROOT', str(root))
    config = tmp_path / 'worker.json'
    config.write_text(json.dumps({'agent_run': {'repo_root': str(tmp_path / 'external-issue')}}))
    assert cli.runtime_command(['worker', 'dispatch', '--config', str(config)])[0] == str(selected)


def test_help_bypasses_broken_runtime_and_legacy_active_launch_is_refused(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    (root / '.run-with-it').mkdir(parents=True)
    (root / '.run-with-it/main-state.json').write_text(json.dumps({'active_pool_issues': ['42'], 'issue_registry': {'42': {'status': 'in_progress'}}}))
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    assert cli.runtime_command(['run', 'pool', '--help']) is None
    with pytest.raises(ValueError, match='legacy|quiescent'):
        cli.runtime_command(['run', 'pool', '--run-root', str(root)])


def test_pair_pin_precedes_enclosing_pool_pin(tmp_path, monkeypatch):
    (tmp_path / '.run-with-it').mkdir()
    (tmp_path / '.run-with-it/engine.json').write_text(json.dumps({'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': sys.executable}))
    pair = tmp_path / '.pair-colleague/runs/discussion'
    pair.mkdir(parents=True)
    selected = tmp_path / 'pair-python'
    selected.touch()
    (pair / 'config.json').write_text(json.dumps({'engine_runtime': {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(selected)}}))
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    assert cli.runtime_command(['pair', 'status', '--run-dir', str(pair)])[0] == str(selected)


def test_implicit_agent_run_refuses_active_legacy_run(tmp_path, monkeypatch):
    (tmp_path / '.run-with-it').mkdir()
    (tmp_path / '.run-with-it/main-state.json').write_text(json.dumps({'active_pool_issues': ['42']}))
    monkeypatch.setenv('AI_SKILLS_RUN_ROOT', str(tmp_path))
    with pytest.raises(ValueError, match='legacy|quiescent'):
        cli.runtime_command(['agent', '--agent', 'codex'])
