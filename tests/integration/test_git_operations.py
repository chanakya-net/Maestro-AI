import importlib
import json
from pathlib import Path
import subprocess

import pytest


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo with spaces'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.test')
    (root / 'source.txt').write_text('baseline\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'baseline')
    git(root, 'branch', 'feature')
    return root


def test_prepare_issue_and_dirty_resume_preserve_work(repository):
    api = importlib.import_module('ai_skills.git_operations')
    result = api.prepare_issue(repository, 'feature', '42', fetch=False)
    worktree = Path(result['worktree'])
    assert result['issue_branch'] == 'feature-issue-42'
    assert result['issue_base_sha'] == git(repository, 'rev-parse', 'feature')
    assert result['issue_base_source'] == 'local'
    (worktree / 'source.txt').write_text('unfinished\n')
    resumed = api.prepare_issue(repository, 'feature', '42', fetch=False)
    assert resumed['resumed'] is True
    assert (worktree / 'source.txt').read_text() == 'unfinished\n'


def test_verified_merge_uses_disposable_tree_and_advances_local_run_tip(repository):
    api = importlib.import_module('ai_skills.git_operations')
    prepared = api.prepare_issue(repository, 'feature', '42', fetch=False)
    worktree = Path(prepared['worktree'])
    (worktree / 'new.txt').write_text('implementation\n')
    git(worktree, 'add', '.')
    git(worktree, 'commit', '-m', 'implementation')
    root_head = git(repository, 'rev-parse', 'HEAD')
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'])
    assert result['outcome'] == 'completed'
    assert result['verification']['passed'] is True
    assert git(repository, 'rev-parse', 'HEAD') == root_head
    assert not (repository / 'new.txt').exists()
    next_issue = api.prepare_issue(repository, 'feature', '43', fetch=False)
    assert next_issue['issue_base_sha'] == result['commit_sha']
    assert (Path(next_issue['worktree']) / 'new.txt').read_text() == 'implementation\n'


def test_failed_verification_never_advances_integration_tip(repository):
    import sys
    api = importlib.import_module('ai_skills.git_operations')
    prepared = api.prepare_issue(repository, 'feature', '42', fetch=False)
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'], verify_commands=[[sys.executable, '-c', 'raise SystemExit(3)']])
    assert result['outcome'] == 'merge_failed'
    assert result['verification']['passed'] is False
    assert Path(result['merge_worktree']).is_dir()
    assert api.prepare_issue(repository, 'feature', '43', fetch=False)['issue_base_sha'] == git(repository, 'rev-parse', 'feature')


def test_conflict_preserves_recovery_tree(repository):
    api = importlib.import_module('ai_skills.git_operations')
    prepared = api.prepare_issue(repository, 'feature', '42', fetch=False)
    worktree = Path(prepared['worktree'])
    (worktree / 'source.txt').write_text('issue\n')
    git(worktree, 'commit', '-am', 'issue')
    git(repository, 'checkout', 'feature')
    (repository / 'source.txt').write_text('shared\n')
    git(repository, 'commit', '-am', 'shared')
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'])
    assert result['outcome'] == 'merge_failed'
    assert result['reason'] == 'merge-conflict'
    assert Path(result['merge_worktree']).is_dir()
    assert (repository / 'source.txt').read_text() == 'shared\n'


def test_existing_unregistered_directory_is_never_reset(repository):
    api = importlib.import_module('ai_skills.git_operations')
    path = repository / '.run-with-it' / 'worktrees' / 'issue-42'
    path.mkdir(parents=True)
    (path / 'keep.txt').write_text('user work')
    with pytest.raises(ValueError, match='unowned|registered|metadata'):
        api.prepare_issue(repository, 'feature', '42', fetch=False)
    assert (path / 'keep.txt').read_text() == 'user work'


@pytest.mark.parametrize('mutation', ['tracked', 'index', 'head'])
def test_verification_cannot_validate_a_different_tree_or_head(repository, mutation):
    import sys
    api = importlib.import_module('ai_skills.git_operations')
    prepared = api.prepare_issue(repository, 'feature', '42', fetch=False)
    worktree = Path(prepared['worktree'])
    (worktree / 'new.txt').write_text('implementation\n')
    git(worktree, 'add', '.')
    git(worktree, 'commit', '-m', 'implementation')
    if mutation == 'head':
        commands = [['git', 'reset', '--hard', 'HEAD^']]
    else:
        commands = [[sys.executable, '-c', "from pathlib import Path;Path('source.txt').write_text('changed during verification\\n')"]]
        if mutation == 'index': commands.append(['git', 'add', 'source.txt'])
        commands.append([sys.executable, '-c', "from pathlib import Path;assert Path('source.txt').read_text() == 'changed during verification\\n'"])
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'], verify_commands=commands)
    assert result['outcome'] == 'merge_failed'
    assert result['verification']['passed'] is False
    assert result['reason'] in ('verification-modified-tree', 'verification-changed-head')
    assert Path(result['merge_worktree']).is_dir()
    assert not (repository / '.run-with-it/integration.json').exists()
    if mutation != 'head':
        assert (Path(result['merge_worktree']) / 'source.txt').read_text() == 'changed during verification\n'


def _declare_gate(repository, commands):
    gate = repository / '.maestro' / 'merge-gate.json'
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(json.dumps({'schema_version': 1, 'commands': commands}))
    git(repository, 'checkout', '-q', 'feature')
    git(repository, 'add', '.maestro')
    git(repository, 'commit', '-qm', 'declare merge gate')
    git(repository, 'checkout', '-q', 'main')


def _issue_commit(api, repository, issue, relative, text):
    prepared = api.prepare_issue(repository, 'feature', issue, fetch=False)
    worktree = Path(prepared['worktree'])
    (worktree / relative).parent.mkdir(parents=True, exist_ok=True)
    (worktree / relative).write_text(text)
    git(worktree, 'add', '.')
    git(worktree, 'commit', '-qm', 'issue work')
    return prepared, worktree


def test_repo_merge_gate_blocks_cross_cutting_regression(repository):
    import sys
    api = importlib.import_module('ai_skills.git_operations')
    check = "import pathlib,sys; sys.exit('untranslated' in pathlib.Path('locales.txt').read_text())"
    (repository / 'locales.txt').write_text('ok\n')
    git(repository, 'add', '.'); git(repository, 'commit', '-qm', 'locales')
    git(repository, 'branch', '-f', 'feature', 'main')
    _declare_gate(repository, [{'name': 'i18n coverage', 'argv': [sys.executable, '-c', check]}])
    prepared, _ = _issue_commit(api, repository, '911', 'locales.txt', 'untranslated\n')
    tip = git(repository, 'rev-parse', 'feature')
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'])
    assert (result['outcome'], result['reason'], result['failed_gate']) == ('merge_failed', 'merge-gate-failed', 'i18n coverage')
    assert api.prepare_issue(repository, 'feature', '912', fetch=False)['issue_base_sha'] == tip


def test_repo_merge_gate_is_read_from_shared_tip_and_honours_when_changed(repository):
    import sys
    api = importlib.import_module('ai_skills.git_operations')
    _declare_gate(repository, [
        {'name': 'frontend', 'argv': [sys.executable, '-c', 'raise SystemExit(4)'], 'when_changed': ['web/*']},
        {'name': 'always', 'argv': [sys.executable, '-c', 'import pathlib; pathlib.Path("probe").exists() or exit(5)'],
         'cwd': 'sub'},
    ])
    # The issue deletes the gate and creates sub/probe; the gate from the shared tip still runs.
    prepared = api.prepare_issue(repository, 'feature', '42', fetch=False)
    worktree = Path(prepared['worktree'])
    (worktree / 'sub').mkdir()
    (worktree / 'sub' / 'probe').write_text('x')
    git(worktree, 'rm', '-q', '.maestro/merge-gate.json')
    git(worktree, 'add', '.')
    git(worktree, 'commit', '-qm', 'issue')
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'])
    assert result['outcome'] == 'completed', result
    assert result['merge_gate']['commands_run'] == ['always']
    assert result['merge_gate']['skipped'] == ['frontend']


@pytest.mark.parametrize('gate', [
    '{not json',
    json.dumps({'schema_version': 2, 'commands': []}),
    json.dumps({'schema_version': 1, 'commands': [{'argv': []}]}),
    json.dumps({'schema_version': 1, 'commands': [{'argv': ['true'], 'cwd': '../outside'}]}),
    json.dumps({'schema_version': 1, 'commands': [{'argv': ['true'], 'timeout_seconds': 0}]}),
])
def test_invalid_repo_merge_gate_fails_closed(repository, gate):
    api = importlib.import_module('ai_skills.git_operations')
    path = repository / '.maestro' / 'merge-gate.json'
    path.parent.mkdir()
    path.write_text(gate)
    git(repository, 'checkout', '-q', 'feature'); git(repository, 'add', '.maestro'); git(repository, 'commit', '-qm', 'gate')
    git(repository, 'checkout', '-q', 'main')
    prepared, _ = _issue_commit(api, repository, '42', 'new.txt', 'x\n')
    result = api.merge_issue(repository, 'feature', prepared['issue_branch'])
    assert (result['outcome'], result['reason']) == ('merge_failed', 'invalid-merge-gate')
