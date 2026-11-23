import json
import subprocess

import pytest

from ai_skills import check_scopes
from ai_skills.verification import check_evidence_reason

MAP = {'schema_version': 1, 'scopes': [
    {'name': 'frontend', 'check_prefixes': ['cd web && '], 'paths': ['web/**']},
    {'name': 'contracts', 'check_prefixes': ['cd web && bun run e2e'], 'paths': ['contracts/*.json']},
]}
BUILD, E2E, BACKEND = 'cd web && bun run build', 'cd web && bun run e2e', 'dotnet test'
CHECKS = [BUILD, E2E, BACKEND]


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def commit(root, files, message):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        git(root, 'add', name)
    git(root, 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    base = commit(root, {check_scopes.SCOPE_FILE: json.dumps(MAP), 'web/app.ts': '1', 'api/h.cs': '1',
                         'contracts/v.json': '{}'}, 'base')
    start = commit(root, {'api/h.cs': '2'}, 'impl')
    return root, base, start


def skippable(root, base, start, end, checks=CHECKS):
    return check_scopes.skippable_checks(root, base_sha=base, from_sha=start, to_sha=end, required_checks=checks)


def test_backend_only_change_skips_scoped_checks_only(repo):
    root, base, start = repo
    end = commit(root, {'api/h.cs': '3'}, 'fix')
    assert skippable(root, base, start, end) == [BUILD, E2E]


def test_change_under_any_scope_of_a_check_keeps_it(repo):
    root, base, start = repo
    end = commit(root, {'contracts/v.json': '{"a": 1}'}, 'contract')
    # The e2e check matches both scopes, so a contract change keeps it; build is unaffected.
    assert skippable(root, base, start, end) == [BUILD]
    end = commit(root, {'web/app.ts': '2'}, 'frontend')
    assert skippable(root, base, end, end) == []
    assert skippable(root, base, start, end) == []


def test_map_changes_and_worktree_edits_never_widen_skips(repo):
    root, base, start = repo
    wider = {**MAP, 'scopes': [{'check_prefixes': ['dotnet'], 'paths': ['nothing/**']}]}
    end = commit(root, {check_scopes.SCOPE_FILE: json.dumps(wider), 'api/h.cs': '3'}, 'edit map')
    assert skippable(root, base, start, end) == []
    # The worktree copy is never read: only the base commit's map counts.
    (root / check_scopes.SCOPE_FILE).write_text(json.dumps(wider), encoding='utf-8')
    later = commit(root, {'api/h.cs': '4'}, 'fix')
    assert BACKEND not in skippable(root, base, end, later)


def test_missing_or_invalid_map_and_bad_shas_skip_nothing(tmp_path, repo):
    root, base, start = repo
    end = commit(root, {'api/h.cs': '3'}, 'fix')
    assert skippable(root, 'f' * 40, start, end) == []
    assert skippable(root, base, 'HEAD', end) == []
    assert skippable(root, base, start, start) == []


@pytest.mark.parametrize('text', [
    '{}', '[]', '{"schema_version": 2, "scopes": []}',
    json.dumps({'schema_version': 1, 'scopes': [{'check_prefixes': [], 'paths': ['a']}]}),
    json.dumps({'schema_version': 1, 'scopes': [{'check_prefixes': ['a'], 'paths': ['/etc/**']}]}),
    json.dumps({'schema_version': 1, 'scopes': [{'check_prefixes': ['a'], 'paths': ['../x']}]}),
    json.dumps({'schema_version': 1, 'scopes': [{'check_prefixes': ['a'], 'paths': ['x'], 'extra': 1}]}),
])
def test_invalid_maps_are_rejected(text):
    with pytest.raises(ValueError):
        check_scopes.parse_scopes(text)


@pytest.mark.parametrize(('path', 'pattern', 'expected'), [
    ('web/a/b.ts', 'web/**', True), ('web', 'web/**', False), ('webx/a.ts', 'web/**', False),
    ('a/b/c.json', '**/*.json', True), ('c.json', '**/*.json', True), ('a/c.json', '*.json', False),
    ('a.cs', 'a.?s', True),
])
def test_glob_matching(path, pattern, expected):
    assert check_scopes.path_matches(path, pattern) is expected


def evidence(*checks):
    return {'passed': True, 'commit_sha': 'a' * 40, 'checks': list(checks)}


def test_validator_accepts_only_dispatcher_approved_skips():
    ran = {'command': BACKEND, 'passed': True, 'evidence': 'exit 0'}
    skip = {'command': BUILD, 'skipped': True, 'evidence': 'path-scoped'}
    common = {'expected_commit': 'a' * 40, 'required_checks': [BUILD, BACKEND]}
    assert check_evidence_reason(evidence(ran, skip), **common, skippable_checks={BUILD}) == ''
    assert check_evidence_reason(evidence(ran, skip), **common) == 'verification-check-skip-not-allowed'
    claimed = {**skip, 'passed': True}
    assert check_evidence_reason(evidence(ran, claimed), **common, skippable_checks={BUILD}) == \
        'verification-check-skip-not-allowed'
    assert check_evidence_reason(evidence(ran, {**skip, 'skipped': 'yes'}), **common, skippable_checks={BUILD}) == \
        'verification-checks-incomplete'
    assert check_evidence_reason(evidence(ran, {**skip, 'evidence': ' '}), **common, skippable_checks={BUILD}) == \
        'verification-check-not-passed'
    # Omitting a skippable check is still incomplete: every check must be listed once.
    assert check_evidence_reason(evidence(ran), **common, skippable_checks={BUILD}) == 'verification-checks-incomplete'


def test_check_scope_cli_reports_skippable_checks(repo, capsys):
    root, base, start = repo
    commit(root, {'api/h.cs': '3'}, 'fix')
    from ai_skills.artifacts import main
    args = ['check-scope', '--repo-root', str(root), '--issue-base-sha', base, '--from', start]
    for check in CHECKS:
        args += ['--required-check', check]
    assert main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['skippable_checks'] == [BUILD, E2E]
    assert output['to_sha'] == git(root, 'rev-parse', 'HEAD')


def test_check_scope_working_tree_previews_uncommitted_and_untracked_changes(repo, capsys):
    root, base, start = repo
    from ai_skills.artifacts import main
    base_args = ['check-scope', '--repo-root', str(root), '--issue-base-sha', base, '--from', start]
    check_args = [item for check in CHECKS for item in ('--required-check', check)]

    # Clean tree (no uncommitted changes): skips nothing.
    assert main(base_args + ['--working-tree'] + check_args) == 0
    assert json.loads(capsys.readouterr().out) == {'working_tree': True, 'skippable_checks': []}

    # Uncommitted backend-only edit: previews frontend skips before committing.
    (root / 'api/h.cs').write_text('3', encoding='utf-8')
    assert main(base_args + ['--working-tree'] + check_args) == 0
    assert json.loads(capsys.readouterr().out) == {'working_tree': True, 'skippable_checks': [BUILD, E2E]}

    # Untracked frontend file: fails closed and keeps frontend checks.
    untracked = root / 'web/new.ts'
    untracked.write_text('export const x = 1;', encoding='utf-8')
    assert main(base_args + ['--working-tree'] + check_args) == 0
    assert json.loads(capsys.readouterr().out) == {'working_tree': True, 'skippable_checks': []}
    untracked.unlink()

    # Uncommitted edit to the scope map itself: fails closed and skips nothing.
    (root / check_scopes.SCOPE_FILE).write_text(json.dumps({**MAP, 'scopes': [
        {'name': 'frontend', 'check_prefixes': ['dotnet'], 'paths': ['nothing/**']}]}), encoding='utf-8')
    assert main(base_args + ['--working-tree'] + check_args) == 0
    assert json.loads(capsys.readouterr().out) == {'working_tree': True, 'skippable_checks': []}

    # Combining --working-tree with --to is rejected.
    assert main(base_args + ['--working-tree', '--to', 'HEAD'] + check_args) == 2
    assert 'cannot be combined' in capsys.readouterr().err
