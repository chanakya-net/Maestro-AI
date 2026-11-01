"""Review regressions for runtime pin discovery and interrupted installation."""
import json
import os
from pathlib import Path
import socket
import sys
import venv

import psutil
import pytest

from ai_skills import cli, installation
from ai_skills.storage import write_json_atomic


@pytest.mark.parametrize('project_config', [[], ['app'], 42, 'settings', {'app': 'settings'}])
def test_unrelated_project_config_is_not_a_runtime_pin(tmp_path, monkeypatch, project_config):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'config.json').write_text(json.dumps(project_config), encoding='utf-8')
    monkeypatch.chdir(project)
    monkeypatch.delenv('AI_SKILLS_RUN_ROOT', raising=False)
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'absent-assets'))
    assert installation.resolve_runtime(project) == Path(os.path.abspath(sys.executable))
    assert cli.runtime_command(['agent', 'list']) is None


def test_unrelated_config_in_nested_pair_target_does_not_override_pool_pin(tmp_path, monkeypatch):
    (tmp_path / '.run-with-it').mkdir()
    selected = tmp_path / 'pinned-python'; selected.touch()
    write_json_atomic(tmp_path / '.run-with-it/engine.json', {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(selected)})
    nested = tmp_path / 'nested'; nested.mkdir()
    write_json_atomic(nested / 'config.json', ['unrelated settings'])
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    monkeypatch.delenv('AI_SKILLS_RUN_ROOT', raising=False)
    assert cli.runtime_command(['pair', 'status', '--run-dir', str(nested)])[0] == str(selected)


@pytest.mark.parametrize('pin', [None, [], {}, {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': '/missing/python'}])
def test_explicit_invalid_pair_pin_never_falls_back(tmp_path, monkeypatch, pin):
    write_json_atomic(tmp_path / 'config.json', {'engine_runtime': pin})
    monkeypatch.setenv('ASSETS_DEST', str(tmp_path / 'assets'))
    with pytest.raises(ValueError): installation.resolve_runtime(tmp_path)
    with pytest.raises(ValueError): cli.runtime_command(['pair', 'status', '--run-dir', str(tmp_path)])


@pytest.mark.skipif(os.name == 'nt', reason='versioned POSIX venv executable symlinks')
@pytest.mark.parametrize('alias', ['python3.15', 'python3.99'])
def test_interrupted_real_venv_with_versioned_python_link_can_be_cleaned(tmp_path, alias):
    prefix = tmp_path / 'runtime'
    venv.EnvBuilder(with_pip=False).create(prefix)
    link = prefix / 'bin' / alias
    if not link.exists(): link.symlink_to(sys.executable)
    write_json_atomic(prefix / '.installing.json', {'owner': installation.OWNER, 'prefix': str(prefix), 'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time() - 100, 'token': 'abandoned'})
    installation._clean_interrupted(prefix)
    assert not prefix.exists()
    assert Path(sys.executable).is_file()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX interrupted venv symlink guard')
def test_interrupted_cleanup_still_refuses_unexpected_symlinks(tmp_path):
    prefix = tmp_path/'runtime'; prefix.mkdir()
    outside = tmp_path/'user-data'; outside.write_text('preserve me')
    (prefix/'python3.15-escape').symlink_to(outside)
    write_json_atomic(prefix / '.installing.json', {'owner': installation.OWNER, 'prefix': str(prefix), 'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time() - 100, 'token': 'abandoned'})
    with pytest.raises(ValueError, match='escaped interrupted staging'):
        installation._clean_interrupted(prefix)
    assert outside.read_text() == 'preserve me'
    assert prefix.is_dir()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX venv lib64 compatibility link')
def test_interrupted_real_venv_allows_internal_library_alias(tmp_path):
    prefix = tmp_path/'runtime'
    venv.EnvBuilder(with_pip=False).create(prefix)
    library = prefix/'lib'; library.mkdir(exist_ok=True)
    alias = prefix/'lib64'
    if not alias.exists(): alias.symlink_to('lib', target_is_directory=True)
    write_json_atomic(prefix / '.installing.json', {'owner': installation.OWNER, 'prefix': str(prefix), 'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time() - 100, 'token': 'abandoned'})
    installation._clean_interrupted(prefix)
    assert not prefix.exists()
