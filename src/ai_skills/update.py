"""Install the exact current official main commit as a verified managed runtime.

The official repository is shallow-cloned into temporary storage, its tip is
resolved to a full SHA, and the checkout feeds the normal install seam (staging,
payload verification, lock, explicit activation). No local checkout is touched
and existing pinned runs keep their runtime. Selecting agents defaults to the
install detection; preserving the previous selection is a separate concern.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
import json
import re
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

import psutil

from ai_skills.installation import (
    AGENTS, InstallConfig, _absolute, _active_manifest, _recover_installation,
    _registration_receipt, _safe_assets, apply_install, default_assets_dest, plan_install,
)

OFFICIAL_REPOSITORY = 'https://github.com/chanakya-net/Maestro-AI.git'
OFFICIAL_BRANCH = 'main'
CLONE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class UpdateConfig:
    assets_dest: Path | None = None
    home: Path | None = None
    agents: tuple[str, ...] = ()
    dry_run: bool = False
    force: bool = False


def _stop_git(process):
    """Terminate the transport children too, including inherited pipe writers."""
    if os.name == 'posix':
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    else:
        try:
            parent = psutil.Process(process.pid)
            for child in reversed(parent.children(recursive=True)):
                try: child.kill()
                except psutil.NoSuchProcess: pass
            parent.kill()
        except psutil.NoSuchProcess: pass
    process.communicate(timeout=5)


def _git(*args, cwd=None, timeout=CLONE_TIMEOUT_SECONDS):
    git = shutil.which('git')
    if not git: raise ValueError('official source requires git on PATH')
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('GIT_') and not key.upper().endswith('_PROXY')
           and key.upper() not in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    try:
        options = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        with subprocess.Popen([git, *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **options) as process:
            try: stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _stop_git(process)
                raise
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'official source git timed out after {timeout}s') from error
    if process.returncode:
        raise ValueError(f'official source git {args[0]} failed: {stderr.strip() or stdout.strip()}')
    return stdout.strip()


@contextmanager
def official_source(repository=None, branch=None, *, timeout=CLONE_TIMEOUT_SECONDS):
    """Yield ``(checkout, sha)`` for the branch tip; the checkout is removed on exit."""
    repository = repository or OFFICIAL_REPOSITORY
    branch = branch or OFFICIAL_BRANCH
    with tempfile.TemporaryDirectory(prefix='ai-skills-update-') as scratch:
        checkout = Path(scratch) / 'source'
        template = Path(scratch) / 'empty-template'
        template.mkdir()
        try:
            _git('-c', 'core.autocrlf=false', '-c', f'core.hooksPath={template}', '-c', f'core.attributesFile={os.devnull}', 'clone', '--template', str(template), '--quiet', '--depth', '1', '--single-branch', '--no-tags',
                 '--branch', branch, '--', repository, str(checkout), timeout=timeout)
            sha = _git('rev-parse', f'refs/remotes/origin/{branch}^{{commit}}', cwd=checkout, timeout=timeout)
        except (OSError, ValueError, subprocess.SubprocessError) as cause:
            error = ValueError(str(cause) if str(cause).startswith('official source') else f'official source failed: {cause}')
            error.add_note(f'Cannot fetch {repository} branch {branch}; check Git, network access and repository availability, then retry the update.')
            raise error from cause
        yield checkout, sha


def _preflight(config):
    home = _absolute(config.home or Path.home())
    destination = _safe_assets(config.assets_dest or default_assets_dest(), home)
    try:
        if config.agents and (destination / 'runtime.json').is_symlink():
            raise ValueError('active runtime manifest is a symlink')
        _active_manifest(destination, home, automatic=not config.agents)
    except (OSError, ValueError) as error:
        if not (destination / 'runtime.json').exists() and not (destination / 'runtime.json').is_symlink():
            error.add_note('Bootstrap with ai-skills install --source <checkout-or-wheel> from a separate bootstrap environment, or ai-skills update --only <integration>.')
        else:
            error.add_note('Back up the invalid manifest or unowned runtime; inspect ownership, remove only the invalid manifest, and reinstall from a separate bootstrap environment. See README Existing installations.')
        raise


def run_update(config: UpdateConfig, *, repository=None, package_installer=None, registrar=None):
    recovery = _recover_installation(config.assets_dest, config.home, dry_run=config.dry_run)
    try:
        _preflight(config)
        repository = repository or OFFICIAL_REPOSITORY
        with official_source(repository) as (checkout, sha):
            revision = {'repository': repository, 'branch': OFFICIAL_BRANCH, 'commit': sha}
            plan = plan_install(InstallConfig(source=checkout, assets_dest=config.assets_dest, home=config.home,
                                              agents=config.agents, source_revision=revision,
                                              dry_run=config.dry_run, skip_current_revision=not config.force,
                                              refresh_registrations=config.force))
            if config.dry_run:
                return _preview(plan, revision)
            return apply_install(plan, package_installer=package_installer, registrar=registrar)
    except BaseException as error:
        if recovery and not hasattr(error, '_installation_recovery'):
            error._installation_recovery = recovery
        raise


def _copy_is_current(item, fingerprint, prior_by_key):
    if item.get('kind') != 'copy' or (item.get('agent'), item.get('target', '')) not in prior_by_key:
        return False
    target = Path(item.get('target', ''))
    if not (target.exists() or target.is_symlink()):
        return False
    try:
        return _registration_receipt(target).get('fingerprint') == fingerprint
    except ValueError:
        return False


def _backup_entry(agent, target, item):
    """Report a planned backup; edited targets carry the apply-time stamp, so only a pattern is stable."""
    entry = {'integration': agent, 'target': target, 'path': item['backup']}
    if item.get('existing_edited'):
        entry['path'] = re.sub(r'\.edited-\d{8}T\d{6}Z\.', '.edited-<UTC-timestamp>.', item['backup'])
        entry['time_dependent'] = True
    return entry


def _preview(plan, revision):
    """Describe planned update effects without creating persistent state."""
    active_path = plan.assets_dest / 'runtime.json'
    active = json.loads(active_path.read_text(encoding='utf-8')) if active_path.is_file() else {}
    recorded = active.get('source_revision')
    if not plan.config.refresh_registrations and isinstance(recorded, dict) and recorded.get('commit') == revision['commit']:
        return {'source_revision': revision, 'integrations': list(dict.fromkeys(item['agent'] for item in plan.registrations)),
                'backups': [], 'no_op': True}
    committed_path = plan.prefix / 'runtime.json'
    committed = json.loads(committed_path.read_text(encoding='utf-8')) if committed_path.is_file() else {}
    active_by_key = {(item.get('agent'), item.get('target', '')): item
                     for item in active.get('registrations', [])}
    prior_by_key = {(item.get('agent'), item.get('target', '')): item
                    for item in committed.get('registrations', [])}
    no_op = not plan.config.refresh_registrations and active.get('fingerprint') == plan.fingerprint and active.get('source_revision') == revision
    backups, seen_targets = [], set()
    for item in plan.registrations:
        key = (item.get('agent'), item.get('target', ''))
        if not plan.config.refresh_registrations and _copy_is_current(item, plan.fingerprint, prior_by_key):
            if active_by_key.get(key, {}).get('fingerprint') != plan.fingerprint:
                no_op = False
            continue
        no_op = False
        if item.get('kind') == 'copy':
            if item.get('backup'):
                backups.append(_backup_entry(item['agent'], item['target'], item))
            continue
        for path_item in item.get('paths', []):
            target = path_item['target']
            if target in seen_targets:
                continue
            seen_targets.add(target)
            if path_item.get('backup'):
                backups.append(_backup_entry(item['agent'], target, path_item))
    return {
        'source_revision': revision,
        'integrations': list(dict.fromkeys(item['agent'] for item in plan.registrations)),
        'backups': backups,
        'no_op': no_op,
    }


def _report_failure(error):
    print(f'ai-skills update: {str(error) or "interrupted"}', file=sys.stderr)
    recovery = getattr(error, '_installation_recovery', None)
    if recovery:
        description = {'restored': 'prior installation restored', 'incomplete': 'recovery incomplete'}.get(recovery, recovery)
        print(f'ai-skills update: {description}', file=sys.stderr)
    else:
        print('ai-skills update: active installation unchanged', file=sys.stderr)
    for note in getattr(error, '__notes__', ()):
        print(f'ai-skills update: {note}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ai-skills update', description=__doc__)
    parser.add_argument('--assets-dest', type=Path); parser.add_argument('--home', type=Path)
    parser.add_argument('--only', action='append', choices=AGENTS, default=[])
    parser.add_argument('--force', action='store_true', help='refresh managed registrations even at the current revision')
    parser.add_argument('--dry-run', action='store_true', help='preview the resolved update without changing persistent state')
    try:
        options = parser.parse_args(argv)
        with redirect_stdout(sys.stderr):
            manifest = run_update(UpdateConfig(options.assets_dest, options.home, tuple(options.only), options.dry_run, options.force))
        print(json.dumps(manifest, indent=2)); return 0
    except SystemExit as error: return int(error.code or 0)
    except KeyboardInterrupt as error:
        _report_failure(error); return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _report_failure(error)
        return 2
