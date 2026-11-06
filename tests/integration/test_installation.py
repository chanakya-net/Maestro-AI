"""No live provider registration and no network/pip downloads in this suite."""
import json
import os
from pathlib import Path
import sys
import zipfile
import pytest

from ai_skills.installation import InstallConfig, UninstallConfig, plan_install, apply_install, plan_uninstall, apply_uninstall, resolve_runtime


def test_plan_dry_run_writes_nothing(tmp_path):
    destination = tmp_path / 'assets'
    plan = plan_install(InstallConfig(source=Path(__file__).parents[2], assets_dest=destination, home=tmp_path / 'home', agents=('codex',)))
    assert plan.prefix.is_absolute() and plan.pip_commands
    assert not destination.exists()
    assert not (tmp_path / 'home').exists()


def test_missing_pinned_environment_never_falls_back(tmp_path):
    run = tmp_path / 'run'; (run / '.run-with-it').mkdir(parents=True)
    (run / '.run-with-it/engine.json').write_text(json.dumps({'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(tmp_path / 'missing/bin/python')}))
    with pytest.raises(ValueError, match='pinned'):
        resolve_runtime(run, assets_dest=tmp_path / 'assets')

import shutil
import socket
import subprocess
import venv
import psutil
from ai_skills.installation import OWNER, main
from ai_skills.resources import RESOURCE_NAMES, SKILL_NAMES, skill_path
from ai_skills.storage import write_json_atomic


@pytest.fixture
def source_tree(tmp_path):
    source = tmp_path / 'source'
    repository = Path(__file__).parents[2]
    source.mkdir()
    shutil.copy2(repository / 'pyproject.toml', source / 'pyproject.toml')
    for name in ('src', 'assets', 'skills'):
        shutil.copytree(repository / name, source / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return source


def fake_package_installer(plan):
    """Create a native interpreter and fixture wheel payload, no pip/network."""
    venv.EnvBuilder(with_pip=False).create(plan.prefix)
    root = plan.prefix / 'payload/ai_skills'
    root.mkdir(parents=True)
    (root / '__init__.py').write_text('# fixture package ' + plan.version)
    if plan.source.is_dir():
        (root / 'data').mkdir()
        for name in RESOURCE_NAMES: shutil.copy2(plan.source / 'assets' / name, root / 'data' / name)
        shutil.copytree(plan.source / 'skills', root / 'skills')
    else:
        with zipfile.ZipFile(plan.source) as archive:
            for name in archive.namelist():
                if name.startswith(('ai_skills/data/', 'ai_skills/skills/')) and not name.endswith('/'):
                    target = root / Path(name).relative_to('ai_skills')
                    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(archive.read(name))
    return root


def make_config(source, tmp_path, **kwargs):
    return InstallConfig(source=source, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex',), **kwargs)


def test_install_and_reinstall_immutable_runtime_and_owned_skills(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path))
    manifest = apply_install(plan, package_installer=fake_package_installer)
    assert manifest['python'] == str(plan.python) and plan.python.is_file()
    assert Path(manifest['resources']).is_relative_to(plan.prefix)
    assert len(manifest['registrations']) == 7
    target = tmp_path / 'home/.codex/skills/pair-colleague'
    assert 'name: pair-colleague' in (target / 'SKILL.md').read_text()
    assert (target / '.ai-skills-registration.json').is_file()
    before = plan.python.stat().st_mtime_ns
    def must_not_install_again(_): raise AssertionError('same fingerprint must reuse immutable env')
    assert apply_install(plan_install(plan.config), package_installer=must_not_install_again) == manifest
    assert plan.python.stat().st_mtime_ns == before


def test_v1_run_remains_pinned_after_v2_and_symlink_identity_preserved(source_tree, tmp_path):
    first = plan_install(make_config(source_tree, tmp_path))
    apply_install(first, package_installer=fake_package_installer)
    run = tmp_path / 'run'; (run / '.run-with-it').mkdir(parents=True)
    record = {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(first.python)}
    write_json_atomic(run / '.run-with-it/engine.json', record)
    project = source_tree / 'pyproject.toml'; project.write_text(project.read_text().replace('0.1.0.dev0', '0.2.0.dev0'))
    second = plan_install(make_config(source_tree, tmp_path)); assert first.prefix != second.prefix
    apply_install(second, package_installer=fake_package_installer)
    assert resolve_runtime(run, assets_dest=first.assets_dest) == first.python
    assert resolve_runtime(assets_dest=first.assets_dest) == second.python
    assert first.python.is_file()
    pair = tmp_path / 'pair'; pair.mkdir(); write_json_atomic(pair / 'config.json', {'engine_runtime': record})
    assert resolve_runtime(pair, assets_dest=first.assets_dest) == first.python


def test_failed_install_leaves_active_version_and_legacy_files_untouched(source_tree, tmp_path):
    config = make_config(source_tree, tmp_path); first = plan_install(config)
    apply_install(first, package_installer=fake_package_installer)
    old = (first.assets_dest / 'runtime.json').read_bytes()
    legacy = first.assets_dest / 'run-agent.sh'; legacy.write_text('user legacy installation')
    project = source_tree / 'pyproject.toml'; project.write_text(project.read_text().replace('0.1.0.dev0', '0.3.0.dev0'))
    second = plan_install(config)
    def fail(plan):
        (plan.prefix / 'partial').write_text('incomplete')
        raise RuntimeError('simulated pip failure')
    with pytest.raises(RuntimeError): apply_install(second, package_installer=fail)
    assert (first.assets_dest / 'runtime.json').read_bytes() == old
    assert not second.prefix.exists() and legacy.read_text() == 'user legacy installation'


def test_interrupted_staging_cleanup_requires_proven_dead_owner(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path)); plan.prefix.mkdir(parents=True)
    marker = {'owner': OWNER, 'prefix': str(plan.prefix), 'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time() - 100, 'token': 'old'}
    write_json_atomic(plan.prefix / '.installing.json', marker)
    (plan.prefix / 'partial').write_text('old staging')
    apply_install(plan, package_installer=fake_package_installer)
    assert not (plan.prefix / 'partial').exists()
    project = source_tree / 'pyproject.toml'; project.write_text(project.read_text().replace('0.1.0.dev0', '0.4.0.dev0'))
    next_plan = plan_install(make_config(source_tree, tmp_path)); next_plan.prefix.mkdir(parents=True)
    marker.update(prefix=str(next_plan.prefix), create_time=psutil.Process().create_time())
    write_json_atomic(next_plan.prefix / '.installing.json', marker)
    with pytest.raises(ValueError, match='live or unknown'): apply_install(next_plan, package_installer=fake_package_installer)
    assert next_plan.prefix.exists()


def test_uninstall_dry_run_zero_writes_and_retains_all_pinned_versions(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path)); apply_install(plan, package_installer=fake_package_installer)
    config = UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home', dry_run=True)
    uninstall = plan_uninstall(config)
    before = (plan.assets_dest / 'runtime.json').read_bytes()
    assert apply_uninstall(uninstall)['retained_runtimes'] == [str(plan.prefix)]
    assert (plan.assets_dest / 'runtime.json').read_bytes() == before
    actual = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    apply_uninstall(actual)
    assert not (plan.assets_dest / 'runtime.json').exists()
    assert plan.python.is_file()
    assert not (tmp_path / 'home/.codex/skills/pair-colleague').exists()


@pytest.mark.parametrize('target', ['home', 'root', 'unowned', 'symlink', 'edited'])
def test_uninstall_rejects_unsafe_or_unowned_targets(source_tree, tmp_path, target):
    home = tmp_path / 'home'
    if target in ('home', 'root'):
        destination = home if target == 'home' else Path(tmp_path.anchor)
        with pytest.raises(ValueError, match='unsafe'): plan_uninstall(UninstallConfig(assets_dest=destination, home=home))
        return
    if target == 'unowned':
        destination = tmp_path / 'unowned'; destination.mkdir()
        write_json_atomic(destination / 'runtime.json', {'schema_version': 1, 'owner': 'somebody else'})
        with pytest.raises(ValueError, match='unowned'): plan_uninstall(UninstallConfig(assets_dest=destination, home=home))
        return
    plan = plan_install(make_config(source_tree, tmp_path)); apply_install(plan, package_installer=fake_package_installer)
    path = home / '.codex/skills/pair-colleague'
    if target == 'symlink':
        real = tmp_path / 'external'; path.rename(real)
        try: path.symlink_to(real, target_is_directory=True)
        except OSError: pytest.skip('OS denies symlink creation for this user')
    else: (path / 'SKILL.md').write_text('edited by user')
    with pytest.raises(ValueError): plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=home))
    assert path.exists()


def test_registration_adapters_use_local_versioned_payload_not_mutable_remote(source_tree, tmp_path):
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('gemini', 'copilot', 'agy', 'antigravity'))
    plan = plan_install(config); called = []
    manifest = apply_install(plan, package_installer=fake_package_installer, registrar=lambda argv: called.append(list(argv)))
    assert len(called) == 4
    assert all('github.com' not in ' '.join(command) for command in called)
    assert str(plan.prefix / 'registration/gemini') in called[0]
    assert all(str(plan.prefix / 'registration/skills') in command for command in called[1:])
    uninstall = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    undos = []; apply_uninstall(uninstall, registrar=lambda argv: undos.append(list(argv)))
    assert len(undos) == 4


def test_source_or_dependency_change_between_plan_apply_refused_without_writes(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path))
    (source_tree / 'skills/pair-colleague/SKILL.md').write_text('changed after planning')
    with pytest.raises(ValueError, match='changed after planning'): apply_install(plan, package_installer=fake_package_installer)
    assert not plan.assets_dest.exists()


def test_existing_unowned_skills_preserved_and_payload_tamper_blocks_reuse(source_tree, tmp_path):
    config = make_config(source_tree, tmp_path)
    legacy = tmp_path / 'home/.codex/skills/pair-colleague'; legacy.mkdir(parents=True); (legacy / 'SKILL.md').write_text('user-owned legacy')
    with pytest.raises(ValueError, match='unowned skill registration'): plan_install(config)
    assert (legacy / 'SKILL.md').read_text() == 'user-owned legacy'
    from dataclasses import replace
    plan = plan_install(replace(config, replace_existing=True))
    apply_install(plan, package_installer=fake_package_installer)
    uninstall = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    apply_uninstall(uninstall)
    assert (legacy / 'SKILL.md').read_text() == 'user-owned legacy'
    receipt = json.loads((plan.prefix / '.ai-skills-runtime.json').read_text())
    (Path(receipt['package_root']) / 'data/coordinator-rules.md').write_text('tampered')
    with pytest.raises(ValueError, match='payload changed'): apply_install(plan, package_installer=fake_package_installer)


def test_corrupt_pinned_record_and_unknown_engine_never_fall_back(tmp_path):
    run = tmp_path / 'run'; (run / '.run-with-it').mkdir(parents=True)
    record = run / '.run-with-it/engine.json'; record.write_text('{bad')
    with pytest.raises(ValueError): resolve_runtime(run, assets_dest=tmp_path / 'assets')
    write_json_atomic(record, {'schema_version': 1, 'engine': 'unknown', 'engine_version': 1, 'python': sys.executable})
    with pytest.raises(ValueError, match='incompatible'): resolve_runtime(run, assets_dest=tmp_path / 'assets')


def test_cli_list_help_and_dry_run_do_not_mutate(source_tree, tmp_path, capsys):
    assert main(['--list']) == 0 and 'codex' in capsys.readouterr().out
    assert main(['--help']) == 0
    assert main(['--dry-run', '--source', str(source_tree), '--assets-dest', str(tmp_path / 'assets'), '--home', str(tmp_path / 'home'), '--only', 'codex']) == 0
    assert not (tmp_path / 'assets').exists() and not (tmp_path / 'home').exists()


def test_built_wheel_ships_all_skills_and_resources(tmp_path):
    repository = Path(__file__).parents[2]
    output = tmp_path / 'wheelhouse'
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--no-isolation', '--outdir', str(output), str(repository)], check=True, capture_output=True, text=True)
    wheel, = output.glob('*.whl')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert all('ai_skills/data/' + name in names for name in RESOURCE_NAMES)
        assert all('ai_skills/skills/' + name + '/SKILL.md' in names for name in SKILL_NAMES)
        assert not any(name.endswith(('.sh', '.ps1')) for name in names)
    plan = plan_install(InstallConfig(source=wheel, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex',)))
    manifest = apply_install(plan, package_installer=fake_package_installer)
    assert manifest['version'] == plan.version
    assert (Path(manifest['skills']) / 'pair-colleague/SKILL.md').is_file()


def test_uninstall_refuses_additional_unowned_files_and_symlink_runtime_parent(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path)); apply_install(plan, package_installer=fake_package_installer)
    target = tmp_path / 'home/.codex/skills/pair-colleague'
    extra = target / 'user-notes.txt'; extra.write_text('personal notes')
    with pytest.raises(ValueError, match='additional content'): plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    assert extra.read_text() == 'personal notes'


def test_explicit_replacement_backup_restored_after_failed_registration(source_tree, tmp_path):
    from dataclasses import replace
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex', 'gemini'), replace_existing=True)
    legacy = tmp_path / 'home/.codex/skills/pair-colleague'; legacy.mkdir(parents=True)
    (legacy / 'SKILL.md').write_text('legacy pair content'); (legacy / 'notes').write_text('preserve notes')
    plan = plan_install(config)
    def fail(_): raise RuntimeError('registration failed')
    with pytest.raises(RuntimeError, match='registration failed'): apply_install(plan, package_installer=fake_package_installer, registrar=fail)
    assert (legacy / 'SKILL.md').read_text() == 'legacy pair content'
    assert (legacy / 'notes').read_text() == 'preserve notes'
    assert not (plan.assets_dest / 'runtime.json').exists()
    assert not (tmp_path / 'home/.codex/skills/run-with-it').exists()


def test_replacement_backup_and_user_edits_are_guarded_on_uninstall(source_tree, tmp_path):
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex',), replace_existing=True)
    legacy = tmp_path / 'home/.codex/skills/pair-colleague'; legacy.mkdir(parents=True)
    (legacy / 'SKILL.md').write_text('original')
    plan = plan_install(config); manifest = apply_install(plan, package_installer=fake_package_installer)
    r = next(r for r in manifest['registrations'] if r['skill'] == 'pair-colleague')
    backup = Path(r['backup']); assert (backup / 'SKILL.md').read_text() == 'original'
    (backup / 'SKILL.md').write_text('changed backup')
    with pytest.raises(ValueError, match='backup'): plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    assert (legacy / '.ai-skills-registration.json').exists()


def test_unowned_native_registration_collision_needs_explicit_backup(source_tree, tmp_path):
    from dataclasses import replace
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('gemini',))
    legacy = tmp_path / 'home/.gemini/extensions/ai-skill-collections'; legacy.mkdir(parents=True)
    (legacy / 'gemini-extension.json').write_text('legacy extension')
    with pytest.raises(ValueError, match='unowned adapter'): plan_install(config)
    plan = plan_install(replace(config, replace_existing=True)); calls = []
    def adapter(argv):
        calls.append(argv)
        if 'uninstall' in argv:
            if legacy.exists(): shutil.rmtree(legacy)
        else:
            legacy.mkdir(parents=True); (legacy / 'gemini-extension.json').write_text('new extension')
    apply_install(plan, package_installer=fake_package_installer, registrar=adapter)
    assert 'uninstall' in calls[0] and 'install' in calls[1]
    uninstall = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    apply_uninstall(uninstall, registrar=adapter)
    assert (legacy / 'gemini-extension.json').read_text() == 'legacy extension'


def test_legacy_skill_symlink_backed_up_and_restored_without_touching_target(source_tree, tmp_path):
    outside = tmp_path / 'external-skill'; outside.mkdir(); (outside / 'SKILL.md').write_text('original linked skill')
    link = tmp_path / 'home/.codex/skills/pair-colleague'; link.parent.mkdir(parents=True)
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip('OS denies symlink creation for this user')
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex',), replace_existing=True)
    plan = plan_install(config); apply_install(plan, package_installer=fake_package_installer)
    assert not link.is_symlink() and (outside / 'SKILL.md').read_text() == 'original linked skill'
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home')))
    assert link.is_symlink() and link.resolve() == outside
    assert (outside / 'SKILL.md').read_text() == 'original linked skill'


def test_partial_uninstall_updates_manifest_and_preserves_other_registration(source_tree, tmp_path):
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex', 'claude'))
    plan = plan_install(config); apply_install(plan, package_installer=fake_package_installer)
    partial = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home', agents=('codex',)))
    apply_uninstall(partial)
    manifest = json.loads((plan.assets_dest / 'runtime.json').read_text())
    assert {r['agent'] for r in manifest['registrations']} == {'claude'}
    assert (tmp_path / 'home/.claude/skills/pair-colleague').is_dir()
    assert not (tmp_path / 'home/.codex/skills/pair-colleague').exists()
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home')))
    assert not (tmp_path / 'home/.claude/skills/pair-colleague').exists()


def test_assets_only_uninstall_keeps_skills_and_environments(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path)); apply_install(plan, package_installer=fake_package_installer)
    uninstall = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home', agents=('assets',)))
    assert uninstall.paths == (plan.assets_dest / 'runtime.json',)
    apply_uninstall(uninstall)
    assert plan.python.is_file() and (tmp_path / 'home/.codex/skills/pair-colleague').is_dir()


def test_symlink_runtime_parent_is_rejected_before_mutation(source_tree, tmp_path):
    destination = tmp_path / 'assets'; destination.mkdir(); outside = tmp_path / 'external'; outside.mkdir()
    try: (destination / 'runtimes').symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip('OS denies symlink creation for this user')
    with pytest.raises(ValueError, match='symlink escape'): plan_install(make_config(source_tree, tmp_path))
    assert list(outside.iterdir()) == []


def test_upgrade_only_one_agent_keeps_other_owned_registration_in_manifest(source_tree, tmp_path):
    from dataclasses import replace
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex', 'claude'))
    first = plan_install(config); apply_install(first, package_installer=fake_package_installer)
    project = source_tree / 'pyproject.toml'; project.write_text(project.read_text().replace('0.1.0.dev0', '0.8.0.dev0'))
    second = plan_install(replace(config, agents=('codex',))); manifest = apply_install(second, package_installer=fake_package_installer)
    assert {r['agent'] for r in manifest['registrations']} == {'codex', 'claude'}
    assert {r['runtime_prefix'] for r in manifest['registrations'] if r['agent'] == 'claude'} == {str(first.prefix)}
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=second.assets_dest, home=tmp_path / 'home')))
    assert first.python.is_file() and second.python.is_file()
    assert not (tmp_path / 'home/.claude/skills/pair-colleague').exists()


def test_user_edit_after_uninstall_planning_blocks_apply(source_tree, tmp_path):
    plan = plan_install(make_config(source_tree, tmp_path)); apply_install(plan, package_installer=fake_package_installer)
    uninstall = plan_uninstall(UninstallConfig(assets_dest=plan.assets_dest, home=tmp_path / 'home'))
    target = tmp_path / 'home/.codex/skills/pair-colleague/SKILL.md'; target.write_text('user edited after planning')
    with pytest.raises(ValueError): apply_uninstall(uninstall)
    assert target.read_text() == 'user edited after planning'
    assert (plan.assets_dest / 'runtime.json').is_file()


def test_external_registration_and_uninstall_use_planned_home(source_tree, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    import ai_skills.runner as runner
    from types import SimpleNamespace
    selected_home = tmp_path / 'selected home'
    config = InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets',
                           home=selected_home, agents=('gemini',))
    calls = []
    monkeypatch.setattr(runner, 'resolve_executable', lambda argv, env: argv)
    monkeypatch.setattr(installation.subprocess, 'run',
                        lambda argv, **kwargs: (calls.append((argv, kwargs.get('env'))) or SimpleNamespace(returncode=0)))
    apply_install(plan_install(config), package_installer=fake_package_installer)
    assert calls and calls[0][1] is not None
    assert calls[0][1]['HOME'] == str(selected_home)
    assert calls[0][1]['USERPROFILE'] == str(selected_home)
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=config.assets_dest, home=selected_home)))
    assert len(calls) == 2
    assert calls[1][1]['HOME'] == str(selected_home)
    assert calls[1][1]['USERPROFILE'] == str(selected_home)


# --- update from the official main commit (controlled local fixture) ---------

def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _bump(repo, old, new, message):
    project = repo / 'pyproject.toml'
    project.write_text(project.read_text().replace(old, new))
    _git(repo, 'commit', '-qam', message)


@pytest.fixture
def official_repository(source_tree, tmp_path):
    repo = tmp_path / 'official'
    shutil.copytree(source_tree, repo)
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'config', 'user.name', 'Fixture'); _git(repo, 'config', 'user.email', 'fixture@example.invalid')
    _git(repo, 'add', '-A'); _git(repo, 'commit', '-qm', 'initial')
    _git(repo, 'checkout', '-qb', 'feature'); _bump(repo, '0.1.0.dev0', '0.9.0.dev0', 'feature')
    _git(repo, 'checkout', '-q', 'main'); _bump(repo, '0.1.0.dev0', '0.2.0.dev0', 'main tip')
    return repo


def test_official_source_resolves_exact_main_commit_and_cleans_up(official_repository):
    from ai_skills.update import official_source
    before = (_git(official_repository, 'rev-parse', 'HEAD'), _git(official_repository, 'status', '--porcelain'))
    with official_source(official_repository.as_uri()) as (checkout, sha):
        assert sha == _git(official_repository, 'rev-parse', 'main') != _git(official_repository, 'rev-parse', 'feature')
        assert len(sha) == 40 and (checkout / 'pyproject.toml').is_file()
        assert '0.2.0.dev0' in (checkout / 'pyproject.toml').read_text()
    assert not checkout.exists()
    assert before == (_git(official_repository, 'rev-parse', 'HEAD'), _git(official_repository, 'status', '--porcelain'))


def test_official_source_reports_clone_failure_and_cleans_up(tmp_path):
    from ai_skills.update import official_source
    with pytest.raises(ValueError, match='official source'):
        with official_source((tmp_path / 'missing').as_uri()): pass


def test_source_revision_recorded_in_receipt_and_manifest_when_provided(source_tree, tmp_path):
    revision = {'repository': 'file:///x', 'branch': 'main', 'commit': 'a' * 40}
    plan = plan_install(make_config(source_tree, tmp_path, source_revision=revision))
    assert plan.to_record()['source_revision'] == revision
    manifest = apply_install(plan, package_installer=fake_package_installer)
    assert manifest['source_revision'] == revision
    assert json.loads((plan.prefix / '.ai-skills-runtime.json').read_text())['source_revision'] == revision
    plain = plan_install(make_config(source_tree, tmp_path / 'other'))
    assert 'source_revision' not in plain.to_record()
    assert 'source_revision' not in apply_install(plain, package_installer=fake_package_installer)


@pytest.mark.parametrize('bad', ['text', {'repository': 'r', 'branch': 'main', 'commit': 'abc'}, {'commit': 'g' * 40}])
def test_invalid_source_revision_rejected_before_any_write(source_tree, tmp_path, bad):
    with pytest.raises(ValueError, match='source revision'): plan_install(make_config(source_tree, tmp_path, source_revision=bad))
    assert not (tmp_path / 'assets').exists()


def test_update_installs_exact_official_main_commit_and_preserves_checkout(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    before = (_git(official_repository, 'rev-parse', 'HEAD'), _git(official_repository, 'status', '--porcelain'))
    sha = _git(official_repository, 'rev-parse', 'main')
    config = UpdateConfig(assets_dest=tmp_path / 'assets', home=tmp_path / 'home', agents=('codex',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert manifest['version'] == '0.2.0.dev0'
    assert manifest['source_revision'] == {'repository': official_repository.as_uri(), 'branch': 'main', 'commit': sha}
    assert Path(manifest['python']).is_file()
    assert json.loads((tmp_path / 'assets/runtime.json').read_text()) == manifest
    receipt = json.loads((Path(manifest['prefix']) / '.ai-skills-runtime.json').read_text())
    assert receipt['source_revision']['commit'] == sha
    assert before == (_git(official_repository, 'rev-parse', 'HEAD'), _git(official_repository, 'status', '--porcelain'))
    assert _git(official_repository, 'branch', '--show-current') == 'main'


def test_update_keeps_pinned_run_and_reads_older_manifest(official_repository, source_tree, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    project = source_tree / 'pyproject.toml'; project.write_text(project.read_text().replace('0.1.0.dev0', '0.0.9.dev0'))
    old = plan_install(make_config(source_tree, tmp_path)); old_manifest = apply_install(old, package_installer=fake_package_installer)
    assert 'source_revision' not in old_manifest
    run = tmp_path / 'run'; (run / '.run-with-it').mkdir(parents=True)
    write_json_atomic(run / '.run-with-it/engine.json', {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': str(old.python)})
    config = UpdateConfig(assets_dest=old.assets_dest, home=tmp_path / 'home', agents=('codex',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert resolve_runtime(run, assets_dest=old.assets_dest) == old.python
    assert resolve_runtime(assets_dest=old.assets_dest) == Path(manifest['python']) != old.python
    assert old.python.is_file()
    retained = plan_uninstall(UninstallConfig(assets_dest=old.assets_dest, home=tmp_path / 'home')).retained_runtimes
    assert old.prefix in retained and Path(manifest['prefix']) in retained


def test_update_cli_reports_installed_commit(official_repository, tmp_path, monkeypatch, capsys):
    import ai_skills.installation as installation
    import ai_skills.update as update
    monkeypatch.setattr(update, 'OFFICIAL_REPOSITORY', official_repository.as_uri())
    monkeypatch.setattr(installation, '_install_package', fake_package_installer)
    args = ['--assets-dest', str(tmp_path / 'assets'), '--home', str(tmp_path / 'home'), '--only', 'codex']
    assert update.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['source_revision']['commit'] == _git(official_repository, 'rev-parse', 'main')
    assert update.main(['--only', 'bogus']) == 2


def test_update_cli_error_exits_2_without_traceback(tmp_path, monkeypatch, capsys):
    import ai_skills.update as update
    monkeypatch.setattr(update, 'OFFICIAL_REPOSITORY', (tmp_path / 'missing').as_uri())
    assert update.main(['--assets-dest', str(tmp_path / 'assets'), '--home', str(tmp_path / 'home'), '--only', 'codex']) == 2
    assert 'ai-skills update:' in capsys.readouterr().err
