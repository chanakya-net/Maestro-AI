"""No live provider registration and no network/pip downloads in this suite."""
import json
import os
from pathlib import Path
import re
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
import sysconfig
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
    purelib = Path(sysconfig.get_path('purelib', 'venv', vars={'base': str(plan.prefix), 'platbase': str(plan.prefix)}))
    (purelib / 'fixture-payload.pth').write_text(str(root.parent) + '\n')
    (root / '__init__.py').write_text('# fixture package ' + plan.version)
    (root / 'cli.py').write_text('# fixture CLI')
    (root.parent / 'psutil.py').write_text('# fixture dependency')
    if sys.platform == 'win32': (root.parent / 'win32api.py').write_text('# fixture dependency')
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


def test_official_source_ignores_caller_git_dir(official_repository, monkeypatch):
    from ai_skills.update import official_source
    expected = _git(official_repository, 'rev-parse', 'main')
    monkeypatch.setenv('GIT_DIR', str(Path(__file__).parents[2] / '.git'))
    with official_source(official_repository.as_uri()) as (checkout, sha):
        assert sha == expected


def test_official_source_cannot_redirect_official_url(official_repository, monkeypatch):
    import ai_skills.update as update
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', f'url.{official_repository.as_uri()}.insteadOf')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', update.OFFICIAL_REPOSITORY)
    # A deliberately invalid official URL must not be rewritten to the fixture.
    monkeypatch.setattr(update, 'OFFICIAL_REPOSITORY', 'https://127.0.0.1:1/no-repo')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', update.OFFICIAL_REPOSITORY)
    with pytest.raises(ValueError):
        with update.official_source(timeout=5): pass


def test_update_installs_new_revision_when_only_packaged_readme_changes(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    first = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    (official_repository / 'README.md').write_text('new metadata')
    _git(official_repository, 'add', 'README.md'); _git(official_repository, 'commit', '-qm', 'readme')
    second = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert first['prefix'] != second['prefix']
    assert json.loads((Path(second['prefix']) / '.ai-skills-runtime.json').read_text())['source_revision'] == second['source_revision']
    assert Path(first['python']).is_file()


def test_update_rejects_retained_runtime_with_missing_interpreter(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    first = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    active = (config.assets_dest / 'runtime.json').read_bytes()
    Path(first['python']).unlink()
    with pytest.raises(ValueError, match='interpreter missing'):
        run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert (config.assets_dest / 'runtime.json').read_bytes() == active


def test_update_restores_registrations_when_activation_write_fails(official_repository, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    first = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    active = (config.assets_dest / 'runtime.json').read_bytes()
    target = config.home / '.codex/skills/pair-colleague/.ai-skills-registration.json'
    prior = target.read_bytes()
    (official_repository / 'README.md').write_text('revision')
    _git(official_repository, 'add', 'README.md'); _git(official_repository, 'commit', '-qm', 'next')
    write = installation.write_json_atomic
    def fail_active(path, value):
        if path == config.assets_dest / 'runtime.json': raise OSError('activation failed')
        return write(path, value)
    monkeypatch.setattr(installation, 'write_json_atomic', fail_active)
    with pytest.raises(OSError, match='activation failed'):
        run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert target.read_bytes() == prior
    assert (config.assets_dest / 'runtime.json').read_bytes() == active
    assert Path(first['python']).is_file()


def test_update_cli_stdout_stays_json_with_subprocess_output(official_repository, tmp_path, monkeypatch, capfd):
    import ai_skills.installation as installation
    import ai_skills.update as update
    monkeypatch.setattr(update, 'OFFICIAL_REPOSITORY', official_repository.as_uri())
    def noisy(plan):
        subprocess.run([sys.executable, '-c', "print('installer progress')"], check=True, stdout=sys.stdout)
        return fake_package_installer(plan)
    monkeypatch.setattr(installation, '_install_package', noisy)
    assert update.main(['--assets-dest', str(tmp_path / 'assets'), '--home', str(tmp_path / 'home'), '--only', 'codex']) == 0
    captured = capfd.readouterr()
    assert json.loads(captured.out)['source_revision']['commit'] == _git(official_repository, 'rev-parse', 'main')
    assert 'installer progress' in captured.err


def _offline_command(plan):
    script = ("import shutil,sys,sysconfig;from pathlib import Path;s=Path(sys.argv[1]);"
              "r=Path(sysconfig.get_path('purelib'))/'ai_skills';shutil.copytree(s/'src/ai_skills',r);"
              "shutil.copytree(s/'assets',r/'data');shutil.copytree(s/'skills',r/'skills');print('offline install progress')")
    return (str(plan.python), '-I', '-c', script, str(plan.source))


def offline_package_installer(plan):
    """Run the real installer boundary with an offline stand-in for pip install."""
    import dataclasses
    import ai_skills.installation as installation
    assert all(command[1:3] == ('-I', '-m') for command in plan.pip_commands)
    return installation._install_package(dataclasses.replace(plan, pip_commands=(_offline_command(plan),)))


def _shadow(directory, marker):
    """A caller checkout whose venv/pip/ai_skills modules record any import."""
    write = f"from pathlib import Path; Path({str(marker)!r}).write_text(__name__); raise SystemExit(74)\n"
    for module in ('venv.py', 'pip/__init__.py', 'pip/__main__.py', 'ai_skills/__init__.py'):
        target = directory / module; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(write)


def test_update_ignores_caller_python_module_shadowing(official_repository, tmp_path, monkeypatch):
    from ai_skills.update import UpdateConfig, run_update
    caller, path_entry = tmp_path / 'caller', tmp_path / 'pythonpath'
    _shadow(caller, tmp_path / 'cwd-marker'); _shadow(path_entry, tmp_path / 'path-marker')
    monkeypatch.chdir(caller); monkeypatch.setenv('PYTHONPATH', str(path_entry))
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=offline_package_installer)
    assert (Path(manifest['prefix']) / 'pyvenv.cfg').is_file() and Path(manifest['python']).is_file()
    assert Path(manifest['resources']).is_relative_to(manifest['prefix'])
    assert not (tmp_path / 'cwd-marker').exists() and not (tmp_path / 'path-marker').exists()
    assert sorted(p.name for p in caller.iterdir()) == ['ai_skills', 'pip', 'venv.py']


def test_update_rejects_retained_runtime_with_unusable_interpreter(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    first = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    first_sha = first['source_revision']['commit']
    broken = Path(first['python']); broken.unlink(); broken.write_text('not an interpreter'); broken.chmod(0o644)
    (official_repository / 'README.md').write_text('second')
    _git(official_repository, 'add', 'README.md'); _git(official_repository, 'commit', '-qm', 'second')
    second = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    active = (config.assets_dest / 'runtime.json').read_bytes()
    registration = config.home / '.codex/skills/pair-colleague/.ai-skills-registration.json'
    registered = registration.read_bytes()
    _git(official_repository, 'reset', '-q', '--hard', first_sha)
    with pytest.raises(ValueError, match='interpreter'):
        run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert (config.assets_dest / 'runtime.json').read_bytes() == active
    assert registration.read_bytes() == registered
    assert Path(first['prefix']).is_dir() and Path(second['python']).is_file()


def test_update_preserves_original_uninstall_restoration(official_repository, source_tree, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    original = tmp_path / 'home/.codex/skills/pair-colleague'
    original.mkdir(parents=True); (original / 'SKILL.md').write_text('user original skill')
    first = plan_install(make_config(source_tree, tmp_path, replace_existing=True))
    apply_install(first, package_installer=fake_package_installer)
    config = UpdateConfig(first.assets_dest, tmp_path / 'home', ('codex',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert manifest['prefix'] != str(first.prefix)
    plan = plan_uninstall(UninstallConfig(assets_dest=first.assets_dest, home=tmp_path / 'home'))
    assert first.prefix in plan.retained_runtimes
    apply_uninstall(plan)
    assert sorted(p.name for p in original.iterdir()) == ['SKILL.md']
    assert (original / 'SKILL.md').read_text() == 'user original skill'
    others = [tmp_path / 'home/.codex/skills' / name for name in SKILL_NAMES if name != 'pair-colleague']
    assert others and not any(path.exists() for path in others)
    assert not (first.assets_dest / 'runtime.json').exists() and first.python.is_file()


def test_installation_subprocesses_support_redirected_python_stdout(source_tree, tmp_path):
    import contextlib
    import io
    from ai_skills.installation import _run_registration
    plan = plan_install(make_config(source_tree, tmp_path)); plan.prefix.mkdir(parents=True)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        root = offline_package_installer(plan)
        _run_registration([sys.executable, '-c', "print('registration progress')"], home=tmp_path / 'home')
    assert Path(root).is_relative_to(plan.prefix) and (Path(root) / 'skills').is_dir()
    assert 'offline install progress' in buffer.getvalue() and 'registration progress' in buffer.getvalue()


def _fixture_wheel(directory):
    """A minimal offline wheel so real pip runs without any network access."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / 'auditproof-1.0-py3-none-any.whl'
    info = 'auditproof-1.0.dist-info/'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('auditproof.py', 'VALUE = 1\n')
        archive.writestr(info + 'METADATA', 'Metadata-Version: 2.1\nName: auditproof\nVersion: 1.0\n')
        archive.writestr(info + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info + 'RECORD', f'auditproof.py,,\n{info}METADATA,,\n{info}WHEEL,,\n{info}RECORD,,\n')
    return wheel


def _scratch_checkout(path):
    path.mkdir(parents=True); _git(path, 'init', '-q', '-b', 'main')
    (path / 'tracked.txt').write_text('tracked'); _git(path, 'add', '-A')
    _git(path, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'scratch')
    return path


@pytest.mark.parametrize('redirect', ['env-target', 'config-target', 'config-prefix', 'config-root'])
def test_update_ignores_pip_install_destination_overrides(official_repository, tmp_path, monkeypatch, redirect):
    import dataclasses
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    wheels, checkout = _fixture_wheel(tmp_path / 'wheels').parent, _scratch_checkout(tmp_path / 'checkout')
    kind, option = redirect.split('-')
    # Index settings from the caller's pip configuration must still apply: the
    # wheel is only found through the configured find-links with no index.
    lines = ['[global]', 'no-index = true', f'find-links = {wheels}', 'disable-pip-version-check = true', '[install]']
    if kind == 'config': lines.append(f'{option} = {checkout}')
    else: monkeypatch.setenv('PIP_TARGET', str(checkout))
    (tmp_path / 'pip.conf').write_text('\n'.join(lines) + '\n')
    monkeypatch.setenv('PIP_CONFIG_FILE', str(tmp_path / 'pip.conf'))
    def real_pip_installer(plan):
        pip = (str(plan.python), '-I', '-m', 'pip', 'install', '--no-deps', 'auditproof')
        return installation._install_package(dataclasses.replace(plan, pip_commands=(pip, _offline_command(plan))))
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=real_pip_installer)
    purelib = Path(subprocess.run([manifest['python'], '-I', '-c', 'import auditproof;print(auditproof.__file__)'],
                                  check=True, capture_output=True, text=True).stdout.strip())
    assert purelib.is_relative_to(manifest['prefix'])
    assert _git(checkout, 'status', '--porcelain', '--ignored') == ''
    assert sorted(p.name for p in checkout.iterdir()) == ['.git', 'tracked.txt']


def _deterministic_gemini(extension):
    """Install writes content naming the registered payload; uninstall removes it."""
    def adapter(argv):
        if 'uninstall' in argv:
            if extension.exists(): shutil.rmtree(extension)
            return
        extension.mkdir(parents=True); (extension / 'gemini-extension.json').write_text(json.dumps({'source': argv[-1]}))
    return adapter


def _commit_readme(repo, text):
    (repo / 'README.md').write_text(text)
    _git(repo, 'add', 'README.md'); _git(repo, 'commit', '-qm', text)
    return _git(repo, 'rev-parse', 'main')


def test_update_reactivates_retained_revision_with_adapter(official_repository, source_tree, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    extension = tmp_path / 'home/.gemini/extensions/ai-skill-collections'
    extension.mkdir(parents=True); (extension / 'gemini-extension.json').write_text('legacy extension')
    adapter, repository = _deterministic_gemini(extension), official_repository.as_uri()
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=tmp_path / 'home',
                                        agents=('gemini',), replace_existing=True))
    apply_install(legacy, package_installer=fake_package_installer, registrar=adapter)
    config = UpdateConfig(legacy.assets_dest, tmp_path / 'home', ('gemini',))
    update = lambda: run_update(config, repository=repository, package_installer=fake_package_installer, registrar=adapter)
    sha_a = _git(official_repository, 'rev-parse', 'main'); first = update()
    _commit_readme(official_repository, 'revision B'); second = update()
    assert first['prefix'] != second['prefix']
    _git(official_repository, 'reset', '-q', '--hard', sha_a)
    active, installed = (legacy.assets_dest / 'runtime.json').read_bytes(), (extension / 'gemini-extension.json').read_bytes()
    write = installation.write_json_atomic
    def fail_active(path, value):
        if path == legacy.assets_dest / 'runtime.json': raise OSError('activation failed')
        return write(path, value)
    monkeypatch.setattr(installation, 'write_json_atomic', fail_active)
    with pytest.raises(OSError, match='activation failed'): update()
    assert (legacy.assets_dest / 'runtime.json').read_bytes() == active
    assert (extension / 'gemini-extension.json').read_bytes() == installed
    monkeypatch.setattr(installation, 'write_json_atomic', write)
    again = update()
    assert again['prefix'] == first['prefix'] and again['source_revision']['commit'] == sha_a
    assert json.loads((extension / 'gemini-extension.json').read_text())['source'].startswith(first['prefix'])
    assert subprocess.run([second['python'], '-c', 'pass']).returncode == 0
    assert update()['prefix'] == first['prefix']
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=legacy.assets_dest, home=tmp_path / 'home')), registrar=adapter)
    assert (extension / 'gemini-extension.json').read_text() == 'legacy extension'
    assert Path(second['python']).is_file() and Path(first['python']).is_file()


@pytest.mark.parametrize('paths', ['absolute', 'relative'])
def test_ordinary_install_honors_pip_constraints(source_tree, tmp_path, monkeypatch, paths):
    import dataclasses
    import ai_skills.installation as installation
    wheels = _fixture_wheel(tmp_path / 'wheels').parent
    (tmp_path / 'constraints.txt').write_text('auditproof==2.0\n')
    # Relative pip settings resolve against the caller's cwd, as pip itself does.
    caller = tmp_path if paths == 'relative' else tmp_path / 'elsewhere'
    caller.mkdir(exist_ok=True); monkeypatch.chdir(caller)
    locate = (lambda path: os.path.relpath(path, caller)) if paths == 'relative' else str
    monkeypatch.setenv('PIP_NO_INDEX', '1'); monkeypatch.setenv('PIP_FIND_LINKS', locate(wheels))
    monkeypatch.setenv('PIP_DISABLE_PIP_VERSION_CHECK', '1')
    monkeypatch.setenv('PIP_CONSTRAINT', locate(tmp_path / 'constraints.txt'))
    def real_pip_installer(plan):
        pip = (str(plan.python), '-I', '-m', 'pip', 'install', '--no-deps', 'auditproof')
        return installation._install_package(dataclasses.replace(plan, pip_commands=(pip, _offline_command(plan))))
    plan = plan_install(make_config(source_tree, tmp_path))
    with pytest.raises(subprocess.CalledProcessError):
        apply_install(plan, package_installer=real_pip_installer)
    assert not plan.prefix.exists() and not (plan.assets_dest / 'runtime.json').exists()
    (tmp_path / 'constraints.txt').write_text('auditproof==1.0\n')
    manifest = apply_install(plan_install(make_config(source_tree, tmp_path)), package_installer=real_pip_installer)
    assert subprocess.run([manifest['python'], '-I', '-c', 'import auditproof'], check=False).returncode == 0


def test_update_restores_partially_uninstalled_copy_registrations(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    home, repository = tmp_path / 'home', official_repository.as_uri()
    both = UpdateConfig(tmp_path / 'assets', home, ('codex', 'claude'))
    first = run_update(both, repository=repository, package_installer=fake_package_installer)
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=both.assets_dest, home=home, agents=('codex',))))
    assert not any((home / '.codex/skills' / name).exists() for name in SKILL_NAMES)
    def must_not_install_again(_): raise AssertionError('retained revision must reuse its runtime')
    again = run_update(UpdateConfig(both.assets_dest, home, ('codex',)), repository=repository,
                       package_installer=must_not_install_again)
    assert again['prefix'] == first['prefix'] and again['python'] == first['python']
    assert again['source_revision']['commit'] == _git(official_repository, 'rev-parse', 'main')
    assert json.loads((both.assets_dest / 'runtime.json').read_text()) == again
    for agent in ('codex', 'claude'):
        for name in SKILL_NAMES:
            target = home / f'.{agent}/skills' / name
            receipt = json.loads((target / '.ai-skills-registration.json').read_text())
            assert receipt['fingerprint'] == first['fingerprint'] and (target / 'SKILL.md').is_file()
    assert {(r['agent'], r['target']) for r in again['registrations']} == {
        (r['agent'], r['target']) for r in first['registrations']}
    assert subprocess.run([again['python'], '-c', 'pass']).returncode == 0


_FAKE_NPX = '''#!{python}
"""npx stand-in: a project-local bin wins, like npm exec; otherwise the registry package."""
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ['-y']: args = args[1:]
start = Path(os.environ.get('npm_config_prefix') or os.getcwd())
for directory in (start, *start.parents):
    local = directory / 'node_modules/.bin' / args[0]
    if local.is_file(): os.execv(sys.executable, [sys.executable, str(local), *args[1:]])
if args[1] == 'add':
    root = Path(os.environ['HOME']) / '.agents/skills'
    for skill in (Path(args[2]) / 'skills').iterdir():
        shutil.copytree(skill, root / skill.name, dirs_exist_ok=True)
'''


def test_update_adapter_ignores_caller_local_skills_binary(official_repository, tmp_path, monkeypatch):
    from ai_skills.update import UpdateConfig, run_update
    checkout, marker = _scratch_checkout(tmp_path / 'checkout'), tmp_path / 'checkout/checkout-marker'
    local = checkout / 'node_modules/.bin/skills'; local.parent.mkdir(parents=True)
    local.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("local skills ran")\n')
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    npx = bin_dir / 'npx'; npx.write_text(_FAKE_NPX.format(python=sys.executable)); npx.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('npm_config_prefix', str(checkout)); monkeypatch.chdir(checkout)
    before = _git(checkout, 'status', '--porcelain', '--ignored')
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('copilot',))
    manifest = run_update(config, repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert not marker.exists()
    assert _git(checkout, 'status', '--porcelain', '--ignored') == before
    assert manifest['source_revision']['commit'] == _git(official_repository, 'rev-parse', 'main')
    for name in SKILL_NAMES: assert (tmp_path / 'home/.agents/skills' / name / 'SKILL.md').is_file()
    monkeypatch.delenv('npm_config_prefix')
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=config.assets_dest, home=tmp_path / 'home')))
    assert not marker.exists()


def test_update_rolls_back_other_registrations_after_adapter_failure(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    home, repository = tmp_path / 'home', official_repository.as_uri()
    extension = home / '.gemini/extensions/ai-skill-collections'
    adapter = _deterministic_gemini(extension)
    config = UpdateConfig(tmp_path / 'assets', home, ('codex', 'gemini'))
    run_update(config, repository=repository, package_installer=fake_package_installer, registrar=adapter)
    snapshot = lambda: {p: p.read_bytes() for p in (home / '.codex/skills').rglob('*') if p.is_file()}
    active, receipts = (config.assets_dest / 'runtime.json').read_bytes(), snapshot()
    _commit_readme(official_repository, 'revision B')
    def fail_after_write(argv):
        adapter(argv)
        if 'install' in argv: raise subprocess.CalledProcessError(1, argv)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_update(config, repository=repository, package_installer=fake_package_installer, registrar=fail_after_write)
    assert (config.assets_dest / 'runtime.json').read_bytes() == active
    assert snapshot() == receipts
    # The unverified adapter output and its backup stay for recovery, named on the original error.
    notes = getattr(failure.value, '__notes__', [])
    assert any('preserved' in note and str(extension) in note for note in notes)
    assert (extension / 'gemini-extension.json').is_file()


def _skills_adapter(home):
    """npx skills stand-in: add copies into ~/.agents/skills plus the profile root; remove deletes both."""
    profiles = {'github-copilot': '.copilot/skills', 'antigravity': '.gemini/antigravity/skills', 'agy': '.agy/skills'}
    def adapter(argv):
        roots = [home / '.agents/skills', home / profiles[argv[argv.index('-a') + 1]]]
        if 'remove' in argv:
            for root in roots:
                for name in SKILL_NAMES: shutil.rmtree(root / name, ignore_errors=True)
            return
        for skill in (Path(argv[4]) / 'skills').iterdir():
            for root in roots: shutil.copytree(skill, root / skill.name, dirs_exist_ok=True)
    return adapter


def _commit_skill(repo, text):
    skill = repo / 'skills/pair-colleague/SKILL.md'
    skill.write_text(skill.read_text() + '\n' + text + '\n')
    _git(repo, 'commit', '-qam', text)


def test_update_refreshes_shared_adapter_paths(official_repository, source_tree, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, agents = tmp_path / 'home', ('copilot', 'antigravity')
    shared = home / '.agents/skills/pair-colleague'
    shared.mkdir(parents=True); (shared / 'SKILL.md').write_text('user original skill')
    adapter = _skills_adapter(home)
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=home,
                                        agents=agents, replace_existing=True))
    apply_install(legacy, package_installer=fake_package_installer, registrar=adapter)
    config = UpdateConfig(legacy.assets_dest, home, agents)
    update = lambda: run_update(config, repository=official_repository.as_uri(),
                                package_installer=fake_package_installer, registrar=adapter)
    update()
    _commit_skill(official_repository, 'revision B'); second = update()
    assert 'revision B' in (shared / 'SKILL.md').read_text()
    items = [p for r in second['registrations'] for p in r['paths'] if p['target'] == str(shared)]
    assert len(items) == 2 and items[0]['installed_sha256'] == items[1]['installed_sha256']
    assert items[0]['backup'] == items[1]['backup'] and items[0]['original_backup'] == items[1]['original_backup']
    active, installed = (legacy.assets_dest / 'runtime.json').read_bytes(), (shared / 'SKILL.md').read_bytes()
    _commit_skill(official_repository, 'revision C')
    write = installation.write_json_atomic
    def fail_active(path, value):
        if path == legacy.assets_dest / 'runtime.json': raise OSError('activation failed')
        return write(path, value)
    monkeypatch.setattr(installation, 'write_json_atomic', fail_active)
    with pytest.raises(OSError, match='activation failed'): update()
    assert (legacy.assets_dest / 'runtime.json').read_bytes() == active
    assert (shared / 'SKILL.md').read_bytes() == installed
    monkeypatch.setattr(installation, 'write_json_atomic', write)
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=legacy.assets_dest, home=home)), registrar=adapter)
    assert (shared / 'SKILL.md').read_text() == 'user original skill'
    assert not any((home / '.agents/skills' / name).exists() for name in SKILL_NAMES if name != 'pair-colleague')


@pytest.mark.parametrize('change', ['file', 'symlink-ancestor'])
def test_update_rejects_adapter_target_created_during_staging(official_repository, tmp_path, change):
    from ai_skills.update import UpdateConfig, run_update
    home, repository = tmp_path / 'home', official_repository.as_uri()
    run_update(UpdateConfig(tmp_path / 'assets', home, ('codex',)), repository=repository,
               package_installer=fake_package_installer)
    active = (tmp_path / 'assets/runtime.json').read_bytes()
    _commit_readme(official_repository, 'revision B')
    elsewhere = tmp_path / 'elsewhere/pair-colleague/SKILL.md'
    user = home / '.agents/skills/pair-colleague/SKILL.md'
    def concurrent_writer(plan):
        # Another writer appears after planning but before adapter registration.
        if change == 'file': user.parent.mkdir(parents=True)
        else:
            elsewhere.parent.mkdir(parents=True); user.parents[2].mkdir(parents=True)
            user.parents[1].symlink_to(elsewhere.parents[1], target_is_directory=True)
        user.write_text('user skill written during staging')
        return fake_package_installer(plan)
    calls = []
    with pytest.raises(ValueError, match='changed after planning|symlink escape'):
        run_update(UpdateConfig(tmp_path / 'assets', home, ('copilot',)), repository=repository,
                   package_installer=concurrent_writer, registrar=calls.append)
    assert calls == [] and user.read_text() == 'user skill written during staging'
    assert (tmp_path / 'assets/runtime.json').read_bytes() == active


def test_update_keeps_shared_receipts_after_single_adapter_update(official_repository, source_tree, tmp_path):
    from ai_skills.installation import _tree_hash
    from ai_skills.update import UpdateConfig, run_update
    home, agents = tmp_path / 'home', ('copilot', 'antigravity')
    shared = home / '.agents/skills/pair-colleague'
    shared.mkdir(parents=True); (shared / 'SKILL.md').write_text('user original skill')
    adapter = _skills_adapter(home)
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=home,
                                        agents=agents, replace_existing=True))
    apply_install(legacy, package_installer=fake_package_installer, registrar=adapter)
    update = lambda selected: run_update(UpdateConfig(legacy.assets_dest, home, selected), repository=official_repository.as_uri(),
                                         package_installer=fake_package_installer, registrar=adapter)
    first = update(agents)
    retained = next(r for r in first['registrations'] if r['agent'] == 'antigravity')
    specific = [p for p in retained['paths'] if '.gemini/antigravity' in p['target']]
    _commit_skill(official_repository, 'revision B'); second = update(('copilot',))
    assert 'revision B' in (shared / 'SKILL.md').read_text()
    items = [p for r in second['registrations'] for p in r['paths'] if p['target'] == str(shared)]
    assert len(items) == 2 and {p['installed_sha256'] for p in items} == {_tree_hash(shared)}
    assert items[0]['original_backup'] == items[1]['original_backup']
    kept = next(r for r in second['registrations'] if r['agent'] == 'antigravity')
    assert [p for p in kept['paths'] if '.gemini/antigravity' in p['target']] == specific
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=legacy.assets_dest, home=home)), registrar=adapter)
    assert (shared / 'SKILL.md').read_text() == 'user original skill'


def _target_entries(manifest, target):
    entries = [i for r in manifest['registrations'] for i in r.get('paths', [r])]
    return [i for i in entries if i.get('target') == str(target)]


@pytest.mark.parametrize('operation', ['update', 'reinstall'])
def test_update_backs_up_edited_copy_registration_before_replacement(official_repository, source_tree, tmp_path,
                                                                    monkeypatch, operation):
    import ai_skills.update as update
    from ai_skills.installation import _registration_receipt
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    target = home / '.codex/skills/pair-colleague'
    target.mkdir(parents=True); (target / 'SKILL.md').write_text('user original skill')
    first = apply_install(plan_install(make_config(source_tree, tmp_path, replace_existing=True)),
                          package_installer=fake_package_installer)
    original = _target_entries(first, target)[0]['backup']
    (target / 'SKILL.md').write_text('user edited managed skill')
    (target / 'notes').write_text('user notes')
    preview = {}
    def apply_checked(plan, **kwargs):
        preview.update(plan.to_record())
        entry = _target_entries(preview, target)[0]
        assert entry['existing_edited'] is True and not Path(entry['backup']).exists()
        return apply_install(plan, **kwargs)
    if operation == 'update':
        _commit_skill(official_repository, 'revision B')
        monkeypatch.setattr(update, 'apply_install', apply_checked)
        second = update.run_update(update.UpdateConfig(assets, home), repository=official_repository.as_uri(),
                                   package_installer=fake_package_installer)
        assert 'revision B' in (target / 'SKILL.md').read_text()
    else:
        second = apply_checked(plan_install(make_config(source_tree, tmp_path)), package_installer=fake_package_installer)
        assert second['prefix'] == first['prefix']
        assert (target / 'SKILL.md').read_bytes() == (source_tree / 'skills/pair-colleague/SKILL.md').read_bytes()
    entry = _target_entries(second, target)[0]
    backup = Path(entry['backup'])
    assert entry['existing_edited'] is True and entry['existing_owned'] is True
    assert backup == Path(_target_entries(preview, target)[0]['backup'])
    assert backup.parent == Path(second['prefix']) / 'registration-backups/codex'
    assert re.fullmatch(r'pair-colleague\.edited-\d{8}T\d{6}Z\.[0-9a-f]{64}', backup.name)
    assert (backup / 'SKILL.md').read_text() == 'user edited managed skill'
    assert (backup / 'notes').read_text() == 'user notes'
    assert entry['original_backup'] == original
    assert (Path(original) / 'SKILL.md').read_text() == 'user original skill'
    assert {p.name for p in target.iterdir()} == {'SKILL.md', '.ai-skills-registration.json'}
    assert _registration_receipt(target)['fingerprint'] == second['fingerprint']
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)))
    assert (target / 'SKILL.md').read_text() == 'user original skill'
    assert {p.name for p in target.iterdir()} == {'SKILL.md'}
    assert (backup / 'notes').read_text() == 'user notes'


@pytest.mark.parametrize('change', ['append', 'replace'])
def test_update_backs_up_edited_adapter_path_before_replacement(official_repository, source_tree, tmp_path, change):
    from ai_skills.installation import _tree_hash
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    agents = ('copilot', 'antigravity')
    shared = home / '.agents/skills/pair-colleague'
    shared.mkdir(parents=True); (shared / 'SKILL.md').write_text('user original skill')
    adapter = _skills_adapter(home)
    first = apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home,
                                                    agents=agents, replace_existing=True)),
                          package_installer=fake_package_installer, registrar=adapter)
    original = _target_entries(first, shared)[0]['backup']
    if change == 'append':
        skill = shared / 'SKILL.md'; skill.write_text(skill.read_text() + '\nuser edit\n')
    else:
        shutil.rmtree(shared); shared.mkdir(); (shared / 'user').write_text('foreign')
    edited_hash = _tree_hash(shared)
    edited = {p.name: p.read_bytes() for p in shared.iterdir()}
    _commit_skill(official_repository, 'revision B')
    second = run_update(UpdateConfig(assets, home), repository=official_repository.as_uri(),
                        package_installer=fake_package_installer, registrar=adapter)
    assert 'revision B' in (shared / 'SKILL.md').read_text()
    entries = _target_entries(second, shared)
    assert len(entries) == 2 and all(i['existing_edited'] is True for i in entries)
    assert len({i['backup'] for i in entries}) == 1
    backup = Path(entries[0]['backup'])
    assert backup.parent == Path(second['prefix']) / 'registration-backups/copilot'
    assert re.fullmatch(r'[0-9a-f]{64}\.edited-\d{8}T\d{6}Z\.[0-9a-f]{64}', backup.name)
    assert _tree_hash(backup) == edited_hash
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == edited
    assert {i['original_backup'] for i in entries} == {original}
    untouched = _target_entries(second, home / '.copilot/skills/pair-colleague')[0]
    assert 'existing_edited' not in untouched
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)), registrar=adapter)
    assert (shared / 'SKILL.md').read_text() == 'user original skill'
    assert {p.name for p in shared.iterdir()} == {'SKILL.md'}
    assert _tree_hash(backup) == edited_hash


def test_edited_copy_rollback_restores_edit_and_uninstall_keeps_original(official_repository, source_tree, tmp_path,
                                                                       monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    target = home / '.codex/skills/pair-colleague'
    target.mkdir(parents=True); (target / 'SKILL.md').write_text('user original skill')
    apply_install(plan_install(make_config(source_tree, tmp_path, replace_existing=True)),
                  package_installer=fake_package_installer)
    (target / 'SKILL.md').write_text('user edited managed skill')
    (target / 'notes').write_text('user notes')
    edited = {p.name: p.read_bytes() for p in target.iterdir()}
    active = (assets / 'runtime.json').read_bytes()
    _commit_skill(official_repository, 'revision B')
    write = installation.write_json_atomic
    def fail_active(path, value):
        if path == assets / 'runtime.json': raise OSError('activation failed')
        return write(path, value)
    monkeypatch.setattr(installation, 'write_json_atomic', fail_active)
    update = lambda: run_update(UpdateConfig(assets, home), repository=official_repository.as_uri(),
                                package_installer=fake_package_installer)
    with pytest.raises(OSError, match='activation failed') as failure: update()
    assert not getattr(failure.value, '__notes__', [])
    assert {p.name: p.read_bytes() for p in target.iterdir()} == edited
    assert (assets / 'runtime.json').read_bytes() == active
    monkeypatch.setattr(installation, 'write_json_atomic', write)
    second = update()
    assert 'revision B' in (target / 'SKILL.md').read_text() and not (target / 'notes').exists()
    backup = Path(_target_entries(second, target)[0]['backup'])
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == edited
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)))
    assert (target / 'SKILL.md').read_text() == 'user original skill'
    assert {p.name for p in target.iterdir()} == {'SKILL.md'}
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == edited


@pytest.mark.parametrize('default_selection', [False, True])
@pytest.mark.parametrize('agents', [('codex',), ('copilot', 'antigravity')])
def test_update_restores_original_after_recreating_missing_registration(official_repository, source_tree, tmp_path,
                                                                         monkeypatch, agents, default_selection):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    target = home / ('.codex/skills' if agents == ('codex',) else '.agents/skills') / 'pair-colleague'
    target.mkdir(parents=True); (target / 'SKILL.md').write_text('user original skill')
    adapter = None if agents == ('codex',) else _skills_adapter(home)
    kwargs = dict(package_installer=fake_package_installer, registrar=adapter)
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=agents, replace_existing=True))
    apply_install(legacy, **kwargs)
    update = lambda selected: run_update(UpdateConfig(assets, home, selected), repository=official_repository.as_uri(), **kwargs)
    first = update(agents)
    baseline = {(i['original_backup'], i['original_sha256']) for i in _target_entries(first, target)}
    assert len(baseline) == 1 and None not in next(iter(baseline))
    shutil.rmtree(target)
    _commit_skill(official_repository, 'revision B')
    active = (assets / 'runtime.json').read_bytes()
    write = installation.write_json_atomic
    def fail_active(path, value):
        if path == assets / 'runtime.json': raise OSError('activation failed')
        return write(path, value)
    monkeypatch.setattr(installation, 'write_json_atomic', fail_active)
    selected = () if default_selection else agents[:1]
    with pytest.raises(OSError, match='activation failed'): update(selected)
    assert not target.exists() and (assets / 'runtime.json').read_bytes() == active
    monkeypatch.setattr(installation, 'write_json_atomic', write)
    second = update(selected)
    assert 'revision B' in (target / 'SKILL.md').read_text()
    entries = _target_entries(second, target)
    assert len(entries) == len(agents)
    assert {(i['original_backup'], i['original_sha256']) for i in entries} == baseline
    assert not any(i.get('backup') or i.get('existing_sha256') for i in entries)
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)), registrar=adapter)
    assert sorted(p.name for p in target.iterdir()) == ['SKILL.md']
    assert (target / 'SKILL.md').read_text() == 'user original skill'
    assert not (assets / 'runtime.json').exists()


def test_update_restores_gemini_when_replacement_fails_before_write(official_repository, source_tree, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    home, repository = tmp_path / 'home', official_repository.as_uri()
    extension = home / '.gemini/extensions/ai-skill-collections'
    extension.mkdir(parents=True); (extension / 'gemini-extension.json').write_text('legacy extension')
    adapter = _deterministic_gemini(extension)
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=tmp_path / 'assets', home=home,
                                        agents=('gemini',), replace_existing=True))
    apply_install(legacy, package_installer=fake_package_installer, registrar=adapter)
    update = lambda registrar: run_update(UpdateConfig(legacy.assets_dest, home, ('gemini',)), repository=repository,
                                          package_installer=fake_package_installer, registrar=registrar)
    update(adapter)
    active, installed = (legacy.assets_dest / 'runtime.json').read_bytes(), (extension / 'gemini-extension.json').read_bytes()
    _commit_readme(official_repository, 'revision B')
    def fail_before_write(argv):
        if 'install' in argv: raise subprocess.CalledProcessError(1, argv)
        adapter(argv)
    with pytest.raises(subprocess.CalledProcessError) as failure: update(fail_before_write)
    assert not getattr(failure.value, '__notes__', [])
    assert (legacy.assets_dest / 'runtime.json').read_bytes() == active
    assert (extension / 'gemini-extension.json').read_bytes() == installed
    retry = update(adapter)
    assert json.loads((extension / 'gemini-extension.json').read_text())['source'].startswith(retry['prefix'])
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=legacy.assets_dest, home=home)), registrar=adapter)
    assert (extension / 'gemini-extension.json').read_text() == 'legacy extension'


def test_update_cli_reports_incomplete_rollback(official_repository, tmp_path, monkeypatch, capsys):
    import ai_skills.installation as installation
    import ai_skills.update as update
    extension = tmp_path / 'home/.gemini/extensions/ai-skill-collections'
    adapter = _deterministic_gemini(extension)
    monkeypatch.setattr(update, 'OFFICIAL_REPOSITORY', official_repository.as_uri())
    monkeypatch.setattr(installation, '_install_package', fake_package_installer)
    monkeypatch.setattr(installation, '_run_registration', lambda argv, *, home=None: adapter(argv))
    args = ['--assets-dest', str(tmp_path / 'assets'), '--home', str(tmp_path / 'home'), '--only', 'gemini']
    assert update.main(args) == 0; capsys.readouterr()
    _commit_readme(official_repository, 'revision B')
    def fail_after_write(argv, *, home=None):
        adapter(argv)
        if 'install' in argv: raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(installation, '_run_registration', fail_after_write)
    assert update.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'returned non-zero exit status 1' in captured.err
    assert 'rollback incomplete' in captured.err and str(extension) in captured.err
    assert str(tmp_path / 'assets/runtimes') in captured.err and 'registration-backups' in captured.err


def test_update_rejects_retained_runtime_with_missing_dependency(official_repository, tmp_path):
    from ai_skills.update import UpdateConfig, run_update
    def installer(plan):
        root = fake_package_installer(plan)
        shutil.copytree(plan.source / 'src/ai_skills', root, dirs_exist_ok=True)
        purelib = Path(sysconfig.get_path('purelib', 'venv', vars={'base': str(plan.prefix), 'platbase': str(plan.prefix)}))
        (root.parent / 'psutil.py').unlink()
        shutil.copytree(Path(psutil.__file__).parent, purelib / 'psutil')
        (purelib / 'psutil-7.0.dist-info').mkdir()
        return root
    config = UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('codex',))
    update = lambda: run_update(config, repository=official_repository.as_uri(), package_installer=installer)
    sha = _git(official_repository, 'rev-parse', 'main'); first = update()
    _commit_readme(official_repository, 'next revision'); update()
    active = (config.assets_dest / 'runtime.json').read_bytes()
    target = config.home / '.codex/skills/pair-colleague/SKILL.md'; contents = target.read_bytes()
    purelib = Path(sysconfig.get_path('purelib', 'venv', vars={'base': first['prefix'], 'platbase': first['prefix']}))
    shutil.rmtree(purelib / 'psutil')
    _git(official_repository, 'reset', '-q', '--hard', sha)
    with pytest.raises(ValueError, match='interpreter unusable'): update()
    assert (config.assets_dest / 'runtime.json').read_bytes() == active and target.read_bytes() == contents


def test_update_keeps_npm_cache_outside_checkout(official_repository, tmp_path, monkeypatch):
    from ai_skills.update import UpdateConfig, run_update
    if not shutil.which('npx'): pytest.skip('npx unavailable')
    checkout = tmp_path / 'checkout'; checkout.mkdir()
    _git(checkout, 'init', '-q'); (checkout / 'tracked').write_text('content')
    _git(checkout, 'add', 'tracked')
    before = _git(checkout, 'status', '--porcelain', '--ignored')
    monkeypatch.chdir(checkout)
    monkeypatch.setenv('npm_config_cache', str(checkout / '.npm'))
    monkeypatch.setenv('npm_config_offline', 'true')
    with pytest.raises(subprocess.CalledProcessError):
        run_update(UpdateConfig(tmp_path / 'assets', tmp_path / 'home', ('copilot',)),
                   repository=official_repository.as_uri(), package_installer=fake_package_installer)
    assert _git(checkout, 'status', '--porcelain', '--ignored') == before
    assert not (checkout / '.npm').exists()


def test_update_revalidates_uninstall_baseline_under_lock(official_repository, source_tree, tmp_path):
    from ai_skills.update import UpdateConfig, run_update, official_source
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    target = home / '.agents/skills/pair-colleague'; target.mkdir(parents=True)
    (target / 'SKILL.md').write_text('original X')
    adapter = _skills_adapter(home)
    config = InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=('copilot',), replace_existing=True)
    apply_install(plan_install(config), package_installer=fake_package_installer, registrar=adapter)
    run_update(UpdateConfig(assets, home, ('copilot',)), repository=official_repository.as_uri(), package_installer=fake_package_installer, registrar=adapter)
    _commit_readme(official_repository, 'revision B')
    with official_source(official_repository.as_uri()) as (checkout, sha):
        stale = plan_install(InstallConfig(source=checkout, assets_dest=assets, home=home, agents=('copilot',), source_revision={'repository': official_repository.as_uri(), 'branch': 'main', 'commit': sha}))
        apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)), registrar=adapter)
        (target / 'SKILL.md').write_text('original Y')
        apply_install(plan_install(config), package_installer=fake_package_installer, registrar=adapter)
        run_update(UpdateConfig(assets, home, ('copilot',)), repository=official_repository.as_uri(), package_installer=fake_package_installer, registrar=adapter)
        apply_install(stale, package_installer=fake_package_installer, registrar=adapter)
    apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home)), registrar=adapter)
    assert (target / 'SKILL.md').read_text() == 'original Y'


def test_default_update_refreshes_only_manifest_copy_registrations(official_repository, source_tree, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    first = apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home,
                                                     agents=('codex', 'claude'))), package_installer=fake_package_installer)
    removed = next(r for r in first['registrations'] if r['agent'] == 'codex' and r['skill'] != 'pair-colleague')
    first['registrations'].remove(removed)
    write_json_atomic(assets / 'runtime.json', first)
    untouched = Path(removed['target']) / 'SKILL.md'; before = untouched.read_bytes()
    other = home / '.gemini/extensions/ai-skill-collections/user'; other.parent.mkdir(parents=True); other.write_text('user')
    _commit_skill(official_repository, 'new copy payload')
    monkeypatch.setattr(installation, '_detect', lambda home: pytest.fail('unexpected detection'))
    updated = run_update(UpdateConfig(assets, home), repository=official_repository.as_uri(), package_installer=fake_package_installer)
    key = lambda r: (r['agent'], r['kind'], r.get('skill'))
    assert {key(r) for r in updated['registrations']} == {key(r) for r in first['registrations']}
    assert 'new copy payload' in (home / '.codex/skills/pair-colleague/SKILL.md').read_text()
    assert untouched.read_bytes() == before and other.read_text() == 'user'


@pytest.mark.parametrize('agents', [('gemini',), ('copilot', 'antigravity'), ('agy',)])
def test_default_update_refreshes_only_manifest_command_registrations(official_repository, source_tree, tmp_path, monkeypatch, agents):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    adapter = _deterministic_gemini(home / '.gemini/extensions/ai-skill-collections') if agents == ('gemini',) else _skills_adapter(home)
    legacy = plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=agents))
    first = apply_install(legacy, package_installer=fake_package_installer, registrar=adapter)
    for entry in first['registrations']:
        entry['argv'] = ['untrusted']; entry['undo'] = ['untrusted']
    write_json_atomic(assets / 'runtime.json', first)
    _commit_skill(official_repository, 'new command payload')
    monkeypatch.setattr(installation, '_detect', lambda home: pytest.fail('unexpected detection'))
    calls = []
    def registrar(argv):
        calls.append(argv); adapter(argv)
    updated = run_update(UpdateConfig(assets, home), repository=official_repository.as_uri(),
                         package_installer=fake_package_installer, registrar=registrar)
    assert [r['agent'] for r in updated['registrations']] == list(agents)
    installs = [c for c in calls if 'install' in c or 'add' in c]
    assert len(installs) == len(agents) and all(str(updated['prefix']) in ' '.join(c) for c in installs)
    assert all(c != ['untrusted'] for c in calls)
    assert all(r['argv'] != ['untrusted'] and r['undo'] != ['untrusted'] for r in updated['registrations'])
    if agents != ('gemini',): assert 'new command payload' in (home / '.agents/skills/pair-colleague/SKILL.md').read_text()


@pytest.mark.parametrize('state', ['missing', 'empty', 'legacy'])
def test_default_update_without_registrations_never_detects_agents(official_repository, source_tree, tmp_path, monkeypatch, state):
    import ai_skills.installation as installation
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    if state != 'missing':
        first = apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=('codex',))), package_installer=fake_package_installer)
        apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home, agents=('codex',))))
        if state == 'legacy':
            first = json.loads((assets / 'runtime.json').read_text()); first.pop('registrations'); write_json_atomic(assets / 'runtime.json', first)
    monkeypatch.setattr(installation, '_detect', lambda home: pytest.fail('unexpected detection'))
    kwargs = dict(repository=official_repository.as_uri(), package_installer=fake_package_installer, registrar=lambda argv: pytest.fail('unexpected registrar'))
    if state == 'missing':
        with pytest.raises(ValueError, match='install first'): run_update(UpdateConfig(assets, home), **kwargs)
    else:
        updated = run_update(UpdateConfig(assets, home), **kwargs)
        assert updated['registrations'] == [] and not (home / '.claude').exists()


@pytest.mark.parametrize('case', ['copy', 'command', 'owner', 'identity', 'path', 'symlink'])
def test_default_update_rejects_unowned_registration_sources(official_repository, source_tree, tmp_path, case):
    from ai_skills.update import UpdateConfig, run_update
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    agents = ('copilot',) if case == 'command' else ('codex',)
    adapter = _skills_adapter(home) if case == 'command' else None
    first = apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=agents)), package_installer=fake_package_installer, registrar=adapter)
    active = assets / 'runtime.json'
    if case in ('copy', 'command'):
        target = Path(first['registrations'][0]['target'] if case == 'copy' else first['registrations'][0]['paths'][0]['target'])
        shutil.rmtree(target); target.mkdir(parents=True); (target / 'user').write_text('foreign')
        if case == 'command': first['registrations'][0]['paths'][0].pop('installed_sha256')
    elif case == 'owner': first['owner'] = 'foreign'
    elif case == 'identity': first['registrations'][0]['agent'] = 'gemini'
    elif case == 'path': first['registrations'][0]['target'] = str(tmp_path / 'foreign')
    if case not in ('copy', 'symlink'): write_json_atomic(active, first)
    if case == 'symlink':
        other = assets / 'other.json'; active.rename(other); active.symlink_to(other)
    before = active.read_bytes()
    calls = []
    with pytest.raises(ValueError):
        run_update(UpdateConfig(assets, home), repository=official_repository.as_uri(), package_installer=lambda p: calls.append(p), registrar=calls.append)
    assert calls == [] and active.read_bytes() == before
    assert not list((assets / 'runtimes').glob('*/registration-backups'))
    if case in ('copy', 'command'): assert (target / 'user').read_text() == 'foreign'


def test_default_update_reselects_registrations_after_manifest_change(official_repository, source_tree, tmp_path, monkeypatch):
    import ai_skills.installation as installation
    from ai_skills.update import official_source
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=('codex', 'claude'))), package_installer=fake_package_installer)
    monkeypatch.setattr(installation, '_detect', lambda home: pytest.fail('unexpected detection'))
    with official_source(official_repository.as_uri()) as (checkout, sha):
        revision = {'repository': official_repository.as_uri(), 'branch': 'main', 'commit': sha}
        stale = plan_install(InstallConfig(source=checkout, assets_dest=assets, home=home, source_revision=revision))
        apply_uninstall(plan_uninstall(UninstallConfig(assets_dest=assets, home=home, agents=('claude',))))
        updated = apply_install(stale, package_installer=fake_package_installer)
    assert {r['agent'] for r in updated['registrations']} == {'codex'}
    assert not (home / '.claude/skills/pair-colleague').exists()


def test_default_update_rechecks_manifest_symlink_under_lock(official_repository, source_tree, tmp_path):
    from ai_skills.update import official_source
    home, assets = tmp_path / 'home', tmp_path / 'assets'
    apply_install(plan_install(InstallConfig(source=source_tree, assets_dest=assets, home=home, agents=('codex',))), package_installer=fake_package_installer)
    with official_source(official_repository.as_uri()) as (checkout, sha):
        revision = {'repository': official_repository.as_uri(), 'branch': 'main', 'commit': sha}
        stale = plan_install(InstallConfig(source=checkout, assets_dest=assets, home=home, source_revision=revision))
        active = assets / 'runtime.json'; other = assets / 'other.json'
        active.rename(other); active.symlink_to(other)
        with pytest.raises(ValueError, match='owned active runtime manifest'):
            apply_install(stale, package_installer=lambda plan: pytest.fail('unexpected installer'))
