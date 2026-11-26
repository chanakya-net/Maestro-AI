"""Deterministic worktree and merge operations shared by every host platform."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
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


MERGE_GATE_PATH = '.maestro/merge-gate.json'
MERGE_GATE_DEFAULT_TIMEOUT = 1800
MERGE_GATE_MAX_TIMEOUT = 4 * 3600


def _merge_gate(tree, base):
    """Repo-declared post-merge checks, read from the shared branch before this merge.

    Reading the pre-merge tip means an issue branch cannot weaken or delete the
    gate that judges its own merge. Returns None when the repository declares none.
    Schema::

        {"schema_version": 1, "commands": [
          {"name": "frontend i18n coverage", "argv": ["bun", "run", "test", "--run", "src/app/core/i18n"],
           "cwd": "src/frontend", "timeout_seconds": 900, "when_changed": ["src/frontend/*"]}]}
    """
    shown = _git(tree, 'show', f'{base}:{MERGE_GATE_PATH}', check=False)
    if shown.returncode:
        return None
    try:
        gate = json.loads(shown.stdout)
    except ValueError as error:
        raise ValueError(f'{MERGE_GATE_PATH} is not valid JSON: {error}') from error
    if not isinstance(gate, dict) or gate.get('schema_version') != 1 or not isinstance(gate.get('commands'), list):
        raise ValueError(f'{MERGE_GATE_PATH} must be an object with schema_version 1 and a commands list')
    commands = []
    for index, entry in enumerate(gate['commands']):
        label = f'{MERGE_GATE_PATH} commands[{index}]'
        if not isinstance(entry, dict):
            raise ValueError(f'{label} must be an object')
        argv = entry.get('argv')
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError(f'{label}.argv must be a nonempty list of strings')
        cwd = entry.get('cwd', '.')
        if (not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute() or Path(cwd).drive
                or '..' in Path(cwd).parts):
            raise ValueError(f'{label}.cwd must be a relative path inside the repository')
        timeout = entry.get('timeout_seconds', MERGE_GATE_DEFAULT_TIMEOUT)
        if type(timeout) is not int or not 1 <= timeout <= MERGE_GATE_MAX_TIMEOUT:
            raise ValueError(f'{label}.timeout_seconds must be an integer from 1 to {MERGE_GATE_MAX_TIMEOUT}')
        when = entry.get('when_changed', [])
        if not isinstance(when, list) or not all(isinstance(pattern, str) and pattern for pattern in when):
            raise ValueError(f'{label}.when_changed must be a list of glob strings')
        name = entry.get('name') or ' '.join(argv)
        if not isinstance(name, str):
            raise ValueError(f'{label}.name must be a string')
        commands.append({'name': name, 'argv': argv, 'cwd': cwd, 'timeout_seconds': timeout, 'when_changed': when})
    return commands


def _gate_applies(command, changed):
    patterns = command['when_changed']
    return not patterns or any(fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in patterns)


def merge_issue(repo_root, shared_branch, issue_branch, *, verify_commands=(), push=False):
    """Merge in a disposable tree under a lock; failed recovery work is retained.

    Offline progress uses a private integration tip instead of changing a checked
    out shared branch. Project-specific verification should be supplied by the
    coordinator; the default is Git's whitespace/conflict-marker diff check. A
    repository may also declare ``MERGE_GATE_PATH``; those checks always run after
    the coordinator's, so a narrowly scoped issue cannot merge a cross-cutting
    regression (for example missing translations) that breaks later issues.
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
        try:
            gate = _merge_gate(tree, base)
        except ValueError as error:
            return {**result, 'reason': 'invalid-merge-gate', 'evidence': str(error)}
        if gate is not None:
            changed = [line for line in _git(tree, 'diff', '--name-only', base, 'HEAD').stdout.splitlines() if line]
            report = result['merge_gate'] = {'source': f'{base}:{MERGE_GATE_PATH}', 'commands_run': [], 'skipped': []}
            for command in gate:
                if not _gate_applies(command, changed):
                    report['skipped'].append(command['name'])
                    continue
                from .runner import resolve_executable
                # A committed symlink can point outside the tree although the path is relative.
                gate_cwd = (tree / command['cwd']).resolve()
                if not gate_cwd.is_relative_to(tree.resolve()):
                    return {**result, 'reason': 'invalid-merge-gate', 'failed_gate': command['name'],
                            'evidence': f"cwd {command['cwd']!r} resolves outside the repository"}
                try:
                    argv = resolve_executable(tuple(command['argv']), dict(os.environ))
                    checked = subprocess.run(argv, cwd=gate_cwd, capture_output=True, text=True,
                                             timeout=command['timeout_seconds'])
                except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                    return {**result, 'reason': 'merge-gate-failed', 'failed_gate': command['name'], 'evidence': str(error)}
                report['commands_run'].append(command['name'])
                result['verification']['commands_run'].append(list(command['argv']))
                if checked.returncode:
                    return {**result, 'reason': 'merge-gate-failed', 'failed_gate': command['name'],
                            'evidence': (checked.stderr or checked.stdout)[-4000:]}
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
        # Tracked files and the index were proven clean above, so anything left is
        # untracked gate output (node_modules, build artifacts) that would otherwise
        # make a plain remove refuse and orphan a full checkout per merge.
        cleanup = _git(root, 'worktree', 'remove', '--force', str(tree), check=False)
        if cleanup.returncode:
            result['cleanup_pending'] = True
        return result


def preserve_worktree(repo_root, dest_dir):
    """Capture unstaged/staged binary diffs and untracked files into ``dest_dir``."""
    root = Path(repo_root).resolve()
    if Path(_git(root, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() != root:
        raise ValueError('repo-root must identify the repository or worktree root')
    dest = Path(dest_dir).absolute()
    dest.mkdir(parents=True, exist_ok=True)
    head = _git(root, 'rev-parse', 'HEAD').stdout.strip()
    unstaged = subprocess.run(['git', '-C', str(root), 'diff', '--binary'], capture_output=True, timeout=120)
    staged = subprocess.run(['git', '-C', str(root), 'diff', '--cached', '--binary'], capture_output=True, timeout=120)
    if unstaged.returncode or staged.returncode:
        raise ValueError('git diff failed while preserving worktree')
    patch_path = None
    if unstaged.stdout:
        patch_path = str(dest / 'unstaged.patch')
        (dest / 'unstaged.patch').write_bytes(unstaged.stdout)
    cached_path = None
    if staged.stdout:
        cached_path = str(dest / 'staged.patch')
        (dest / 'staged.patch').write_bytes(staged.stdout)
    untracked_raw = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard', '-z'],
        capture_output=True, timeout=120,
    )
    if untracked_raw.returncode:
        raise ValueError('git ls-files failed while preserving worktree')
    untracked_files = [p for p in untracked_raw.stdout.decode('utf-8').split('\0') if p]
    untracked_dir = None
    copied = []
    if untracked_files:
        untracked_root = dest / 'untracked'
        untracked_root.mkdir(parents=True, exist_ok=True)
        untracked_dir = str(untracked_root)
        for rel in untracked_files:
            rel_path = Path(rel)
            if rel_path.is_absolute() or '..' in rel_path.parts:
                continue
            src = root / rel_path
            if not src.is_file() or src.is_symlink():
                continue
            dst = untracked_root / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)
    dirty = bool(patch_path or cached_path or copied)
    return {
        'schema_version': 1,
        'repo_root': str(root),
        'head': head,
        'dirty': dirty,
        'patch': patch_path,
        'cached_patch': cached_path,
        'untracked_dir': untracked_dir,
        'untracked_files': copied,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ai-skills git')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare-issue')
    merge = commands.add_parser('merge-issue')
    preserve = commands.add_parser('preserve-worktree')
    for command in (prepare, merge):
        command.add_argument('--repo-root', type=Path, default=Path.cwd())
        command.add_argument('--shared-branch', required=True)
    prepare.add_argument('--issue', required=True)
    prepare.add_argument('--worktree-root', type=Path)
    prepare.add_argument('--offline', action='store_true')
    merge.add_argument('--issue-branch', required=True)
    merge.add_argument('--verification-file', type=Path)
    merge.add_argument('--push', action='store_true')
    preserve.add_argument('--repo-root', type=Path, default=Path.cwd())
    preserve.add_argument('--dest-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare-issue':
            result = prepare_issue(args.repo_root, args.shared_branch, args.issue, args.worktree_root, fetch=not args.offline)
        elif args.command == 'preserve-worktree':
            result = preserve_worktree(args.repo_root, args.dest_dir)
        else:
            verification = load_json(args.verification_file) if args.verification_file else ()
            result = merge_issue(args.repo_root, args.shared_branch, args.issue_branch, verify_commands=verification, push=args.push)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('outcome', 'completed') == 'completed' else 1
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 2
