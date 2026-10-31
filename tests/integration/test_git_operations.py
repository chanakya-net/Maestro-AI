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
