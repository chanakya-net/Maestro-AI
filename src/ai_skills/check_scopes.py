"""Path-scoped verification: which required checks a modify commit may skip.

A repository opts in by committing ``.ai-skills-check-scopes.json``. Each scope
says that checks starting with one of its ``check_prefixes`` depend only on
files matching its ``paths`` globs. A modify commit that changes no file under
any scope of a check may record that check as skipped instead of re-running it.

The map is read from the issue base commit, never from the worktree, so a
worker cannot widen its own skips. A commit that touches the map, an invalid
or missing map, or any Git failure allows no skips.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SCOPE_FILE = '.ai-skills-check-scopes.json'
_SHA = re.compile('[0-9a-f]{40}')


def _string_list(value, name):
    if (not isinstance(value, list) or not value or
            any(not isinstance(item, str) or not item.strip() for item in value) or len(set(value)) != len(value)):
        raise ValueError(f'check-scopes {name} must be a nonempty list of unique strings')
    return tuple(value)


def _relative_glob(pattern):
    if pattern.startswith('/') or '\\' in pattern or '..' in pattern.split('/'):
        raise ValueError('check-scopes paths must be repository-relative globs')
    return pattern


def parse_scopes(text):
    """Return ``[(check_prefixes, path_globs), ...]`` or raise ``ValueError``."""
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('scopes'), list) \
            or not data['scopes']:
        raise ValueError('check-scopes must have schema_version 1 and a nonempty scopes list')
    scopes = []
    for scope in data['scopes']:
        if not isinstance(scope, dict) or not set(scope) <= {'name', 'check_prefixes', 'paths'}:
            raise ValueError('check-scopes entries allow only name, check_prefixes and paths')
        prefixes = _string_list(scope.get('check_prefixes'), 'check_prefixes')
        paths = tuple(_relative_glob(path) for path in _string_list(scope.get('paths'), 'paths'))
        scopes.append((prefixes, paths))
    return scopes


def _glob_regex(pattern):
    parts, index = [], 0
    while index < len(pattern):
        if pattern.startswith('**/', index):
            parts.append('(?:.*/)?')
            index += 3
        elif pattern.startswith('**', index):
            parts.append('.*')
            index += 2
        elif pattern[index] == '*':
            parts.append('[^/]*')
            index += 1
        elif pattern[index] == '?':
            parts.append('[^/]')
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile(''.join(parts) + r'\Z')


def path_matches(path, pattern):
    return bool(_glob_regex(pattern).match(path))


def _git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise OSError(f'git {args[0]} failed')
    return result.stdout


def load_scopes(root, base_sha):
    try:
        text = _git(root, 'show', f'{base_sha}:{SCOPE_FILE}')
    except OSError:
        return []
    return parse_scopes(text.decode('utf-8'))


def changed_paths(root, from_sha, to_sha):
    raw = _git(root, 'diff', '--name-only', '-z', '--no-renames', from_sha, to_sha, '--')
    return [path for path in raw.decode('utf-8').split('\0') if path]


def changed_paths_working_tree(root, from_sha):
    tracked = _git(root, 'diff', '--name-only', '-z', '--no-renames', from_sha, '--')
    untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    paths = [path for raw in (tracked, untracked) for path in raw.decode('utf-8').split('\0') if path]
    return list(dict.fromkeys(paths))


def _check_globs(check, scopes):
    return [glob for prefixes, globs in scopes if any(check.startswith(prefix) for prefix in prefixes)
            for glob in globs]


def _filter_skippable(scopes, paths, required_checks):
    if not paths or SCOPE_FILE in paths:
        return []
    skippable = []
    for check in required_checks:
        globs = _check_globs(check, scopes)
        if globs and not any(path_matches(path, glob) for path in paths for glob in globs):
            skippable.append(check)
    return skippable


def skippable_checks(root, *, base_sha, from_sha, to_sha, required_checks):
    """Required checks the ``from_sha..to_sha`` change may skip; empty when unsure."""
    if not all(isinstance(sha, str) and _SHA.fullmatch(sha) for sha in (base_sha, from_sha, to_sha)) \
            or from_sha == to_sha:
        return []
    try:
        scopes = load_scopes(root, base_sha)
        if not scopes:
            return []
        paths = changed_paths(root, from_sha, to_sha)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return []
    return _filter_skippable(scopes, paths, required_checks)


def skippable_checks_working_tree(root, *, base_sha, from_sha, required_checks):
    """Preview required checks skippable for uncommitted changes since ``from_sha``."""
    if not all(isinstance(sha, str) and _SHA.fullmatch(sha) for sha in (base_sha, from_sha)):
        return []
    try:
        scopes = load_scopes(root, base_sha)
        if not scopes:
            return []
        paths = changed_paths_working_tree(root, from_sha)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return []
    return _filter_skippable(scopes, paths, required_checks)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ai-skills artifact check-scope', description=(
        'Print which required checks the from..to change (or --working-tree preview) may record as '
        f'skipped under the {SCOPE_FILE} map at the issue base commit.'))
    parser.add_argument('--repo-root', required=True)
    parser.add_argument('--issue-base-sha', required=True)
    parser.add_argument('--from', dest='from_sha', required=True, help='Commit the change starts from (pre-spawn HEAD).')
    parser.add_argument('--to', dest='to_sha', default=None, help='Commit the change ends at (default HEAD).')
    parser.add_argument('--working-tree', action='store_true', default=False,
        help='Preview against tracked working-tree changes and untracked files since --from (before committing).')
    parser.add_argument('--required-check', action='append', default=[], dest='checks')
    args = parser.parse_args(argv)
    if args.working_tree and args.to_sha is not None:
        print('ai-skills artifact check-scope: --working-tree cannot be combined with --to', file=sys.stderr)
        return 2
    if args.working_tree:
        try:
            from_sha = _git(args.repo_root, 'rev-parse', '--verify', args.from_sha + '^{commit}').decode().strip()
        except OSError:
            print('ai-skills artifact check-scope: unknown --from commit', file=sys.stderr)
            return 2
        skippable = skippable_checks_working_tree(args.repo_root, base_sha=args.issue_base_sha,
            from_sha=from_sha, required_checks=args.checks)
        print(json.dumps({'working_tree': True, 'skippable_checks': skippable}))
        return 0
    to_ref = 'HEAD' if args.to_sha is None else args.to_sha
    try:
        from_sha, to_sha = (_git(args.repo_root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
                            for ref in (args.from_sha, to_ref))
    except OSError:
        print('ai-skills artifact check-scope: unknown --from or --to commit', file=sys.stderr)
        return 2
    skippable = skippable_checks(args.repo_root, base_sha=args.issue_base_sha, from_sha=from_sha,
        to_sha=to_sha, required_checks=args.checks)
    print(json.dumps({'to_sha': to_sha, 'skippable_checks': skippable}))
    return 0
