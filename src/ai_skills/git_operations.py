"""Deterministic worktree and merge operations shared by every host platform."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from .git import is_ancestor, resolve_commit
from .storage import DirectoryLock, load_json, write_json_atomic


def _git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'git operation failed')
    return result


def _branch(root, name):
    if not name or _git(root, 'check-ref-format', '--branch', name, check=False).returncode:
        raise ValueError('invalid branch name: ' + str(name))
    return name


def _runtime(root):
    root = Path(root).absolute()
    if Path(_git(root, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() != root.resolve():
        raise ValueError('repo-root must identify the repository root')
    directory = root / '.run-with-it'
    directory.mkdir(parents=True, exist_ok=True)
    common = Path(_git(root, 'rev-parse', '--git-common-dir').stdout.strip())
    if not common.is_absolute():
        common = root / common
    exclude = common / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding='utf-8') if exclude.exists() else ''
    if '/.run-with-it/' not in existing.splitlines():
        with exclude.open('a', encoding='utf-8') as handle:
            handle.write(('\n' if existing and not existing.endswith('\n') else '') + '/.run-with-it/\n')
    return directory


def _base(root, branch, fetch):
    _branch(root, branch)
    remote = f'origin/{branch}'
    if fetch:
        _git(root, 'fetch', 'origin', branch, check=False)
    base = resolve_commit(str(root), remote)
    source = 'remote' if base else 'local'
    base = base or resolve_commit(str(root), branch)
    if not base:
        raise ValueError(f'shared branch is unavailable: {branch}')
    metadata = load_json(root / '.run-with-it' / 'integration.json', missing={})
    if not isinstance(metadata, dict):
        raise ValueError('invalid integration metadata')
    integrated = metadata.get(branch)
    if integrated:
        if not resolve_commit(str(root), integrated):
            raise ValueError('recorded integration commit is missing')
        if is_ancestor(str(root), base, integrated):
            base, source = integrated, 'local-integration'
        elif not is_ancestor(str(root), integrated, base):
            raise ValueError('local integration and shared branch diverged; merge recovery is required')
    return base, source


def prepare_issue(repo_root, shared_branch, issue, worktree_root=None, *, fetch=True):
    """Create an issue worktree once; resume never resets dirty or committed work."""
    root = Path(repo_root).resolve()
    runtime = _runtime(root)
    issue = str(issue)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', issue):
        raise ValueError('invalid issue identifier')
    branch = _branch(root, f'{shared_branch}-issue-{issue}')
    worktree = (Path(worktree_root) if worktree_root else runtime / 'worktrees') / ('issue-' + issue)
    worktree = worktree.absolute()
    metadata_path = runtime / 'issues' / issue / 'base.json'
    with DirectoryLock(runtime / 'issues' / issue / 'prepare'):
        if worktree.exists():
            metadata = load_json(metadata_path, missing=None)
            if not isinstance(metadata, dict) or metadata.get('worktree') != str(worktree):
                raise ValueError('existing worktree has no matching ownership metadata')
            if Path(_git(worktree, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() != worktree.resolve():
                raise ValueError('existing directory is not the registered issue worktree')
            if _git(worktree, 'branch', '--show-current').stdout.strip() != branch:
                raise ValueError('existing worktree uses a different branch')
            return {**metadata, 'resumed': True}
        base, source = _base(root, shared_branch, fetch)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if resolve_commit(str(root), branch):
            raise ValueError('issue branch exists without its owned worktree; inspect before recovery')
        _git(root, 'worktree', 'add', '-b', branch, str(worktree), base)
        result = {'schema_version': 1, 'issue': issue, 'worktree': str(worktree), 'issue_branch': branch,
                  'issue_base_sha': base, 'issue_base_source': source, 'resumed': False}
        write_json_atomic(metadata_path, result)
        return result


def merge_issue(repo_root, shared_branch, issue_branch, *, verify_commands=(), push=False):
    """Merge in a disposable tree under a lock; failed recovery work is retained.

    Offline progress uses a private integration tip instead of changing a checked
    out shared branch. Project-specific verification should be supplied by the
    coordinator; the default is Git's whitespace/conflict-marker diff check.
    """
    root = Path(repo_root).resolve()
    runtime = _runtime(root)
    _branch(root, issue_branch)
    with DirectoryLock(runtime / 'merge', timeout_seconds=120):
        base, _ = _base(root, shared_branch, False)
        tree = runtime / 'merge-worktrees' / uuid.uuid4().hex
        tree.parent.mkdir(parents=True, exist_ok=True)
        _git(root, 'worktree', 'add', '--detach', str(tree), base)
        result = {'outcome': 'merge_failed', 'merge_worktree': str(tree), 'verification': {'passed': False, 'commands_run': []}}
        merged = _git(tree, 'merge', '--no-ff', '--no-edit', issue_branch, check=False)
        if merged.returncode:
            return {**result, 'reason': 'merge-conflict', 'evidence': merged.stderr.strip() or merged.stdout.strip()}
        merged_head = _git(tree, 'rev-parse', 'HEAD').stdout.strip()
        commands = list(verify_commands) or [['git', 'diff', '--check', base, 'HEAD']]
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command or not all(isinstance(arg, str) for arg in command):
                return {**result, 'reason': 'invalid-verification-command'}
            from .runner import resolve_executable
            try:
                argv = resolve_executable(tuple(command), dict(os.environ))
                checked = subprocess.run(argv, cwd=tree, capture_output=True, text=True, timeout=600)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                return {**result, 'reason': 'verification-failed', 'evidence': str(error)}
            result['verification']['commands_run'].append(list(command))
            if checked.returncode:
                return {**result, 'reason': 'verification-failed', 'evidence': (checked.stderr or checked.stdout)[-4000:]}
        head = _git(tree, 'rev-parse', 'HEAD').stdout.strip()
        if head != merged_head:
            return {**result, 'reason': 'verification-changed-head',
                    'evidence': f'Verification changed HEAD from {merged_head} to {head}; recovery tree retained'}
        if (_git(tree, 'diff', '--quiet', check=False).returncode
                or _git(tree, 'diff', '--cached', '--quiet', check=False).returncode):
            return {**result, 'reason': 'verification-modified-tree',
                    'evidence': 'Verification modified tracked files or the index; commit and re-verify the retained recovery tree'}
        result['verification'].update(passed=True, evidence='Configured merge verification commands passed for the unchanged merged commit')
        if push and _git(tree, 'push', 'origin', f'{head}:refs/heads/{shared_branch}', check=False).returncode:
            return {**result, 'reason': 'push-failed', 'commit_sha': head}
        from .storage import StateStore
        StateStore(runtime / 'integration.json').transaction(lambda metadata: metadata.update({shared_branch: head}))
        result.update(outcome='completed', reason='', commit_sha=head)
        cleanup = _git(root, 'worktree', 'remove', str(tree), check=False)
        if cleanup.returncode:
            result['cleanup_pending'] = True
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ai-skills git')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare-issue')
    merge = commands.add_parser('merge-issue')
    for command in (prepare, merge):
        command.add_argument('--repo-root', type=Path, default=Path.cwd())
        command.add_argument('--shared-branch', required=True)
    prepare.add_argument('--issue', required=True)
    prepare.add_argument('--worktree-root', type=Path)
    prepare.add_argument('--offline', action='store_true')
    merge.add_argument('--issue-branch', required=True)
    merge.add_argument('--verification-file', type=Path)
    merge.add_argument('--push', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare-issue':
            result = prepare_issue(args.repo_root, args.shared_branch, args.issue, args.worktree_root, fetch=not args.offline)
        else:
            verification = load_json(args.verification_file) if args.verification_file else ()
            result = merge_issue(args.repo_root, args.shared_branch, args.issue_branch, verify_commands=verification, push=args.push)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('outcome', 'completed') == 'completed' else 1
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 2
