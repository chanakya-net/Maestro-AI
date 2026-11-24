"""Isolated venv runtime installation, tree hashing, and skill registration adapters."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
import email
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import uuid
import zipfile

import psutil

from ai_skills.dependency_constraints import _satisfies
from ai_skills.resources import RESOURCE_NAMES, SKILL_NAMES
from ai_skills.storage import DirectoryLock, load_json, replace_file_atomic, write_json_atomic


from ai_skills.installation.plan import (
    AGENTS, OWNER, PROFILES, InstallConfig, InstallationPlan,
    _absolute, _active_manifest, _backup_stamp, _carry_original, _check_source_revision,
    _dependency_pins, _detect, _external_paths, _hash, _manifest_registrations,
    _object, _owned_backup, _python, _safe_assets, _source, _transition_backup,
    _contains_symlink, _managed_receipt, _owned_receipt, _receipt,
    _registration_receipt, _require_no_transaction, _transaction_path,
    _tree_hash, _uninstall_view, default_assets_dest, plan_install,
)





def _isolated_environment():
    # Caller PYTHONPATH/PYTHONHOME/PYTHONSTARTUP must not shadow installer modules.
    return {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}


# Index, trust and network settings pip may inherit. Install destinations
# (target/prefix/root/user/src), logs and requirement injection never pass.
PIP_SETTINGS = frozenset({
    'index-url', 'extra-index-url', 'no-index', 'find-links', 'trusted-host', 'cert', 'client-cert',
    'proxy', 'timeout', 'retries', 'keyring-provider', 'only-binary', 'no-binary', 'prefer-binary',
    'pre', 'no-cache-dir', 'disable-pip-version-check', 'no-input', 'progress-bar', 'quiet', 'verbose'})
_PIP_SECTIONS = {'global': 0, 'install': 1, ':env:': 2}  # pip precedence, lowest first


def _pip_environment(python, base, cwd):
    """Carry allowlisted pip settings as variables; disable every pip config file."""
    listing = subprocess.run([str(python), '-I', '-m', 'pip', 'config', 'list'], capture_output=True,
                             text=True, check=True, cwd=cwd, env=base).stdout
    settings = []
    for line in listing.splitlines():
        name, separator, value = line.partition('=')
        section, _, key = name.rpartition('.')
        if separator and section in _PIP_SECTIONS and key in PIP_SETTINGS:
            settings.append((_PIP_SECTIONS[section], key, ast.literal_eval(value)))
    env = {key: value for key, value in base.items() if not key.upper().startswith('PIP_')}
    for _, key, value in sorted(settings, key=lambda setting: setting[0]):
        env['PIP_' + key.upper().replace('-', '_')] = str(value)
    env['PIP_CONFIG_FILE'] = os.devnull  # pip then loads no global, user, site or env config file
    return env


def _run_child(argv, *, cwd=None, env=None):
    """Run a child whose progress follows Python's stdout, with or without a descriptor."""
    stream = getattr(sys, 'stdout', None)
    try: descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError): descriptor = None  # io.UnsupportedOperation
    if descriptor is not None:
        stream.flush()
        return subprocess.run(argv, check=True, cwd=cwd, env=env, stdout=descriptor)
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, text=True, errors='replace')
    if stream is not None and result.stdout: stream.write(result.stdout)
    result.check_returncode()
    return result


def _runtime_root(python, *, verify_dependencies=False):
    imports = 'import ai_skills.cli,psutil,sys;exec("import win32api") if sys.platform == "win32" else None;' if verify_dependencies else ''
    code = imports + 'import ai_skills,json;from pathlib import Path;print(json.dumps(str(Path(ai_skills.__file__).parent)))'
    result = subprocess.run([str(python), '-I', '-c', code], capture_output=True, text=True, check=True,
                            cwd=Path(python).parents[1], env=_isolated_environment(), timeout=30)
    return Path(json.loads(result.stdout))


def _verify_interpreter(python, package_root):
    """A retained runtime must launch and import its own recorded package."""
    if not python.is_file(): raise ValueError(f'installed venv interpreter missing: {python}')
    try: root = _runtime_root(python, verify_dependencies=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'installed venv interpreter unusable: {python}: {error}') from error
    if root.resolve() != Path(package_root).resolve():
        raise ValueError(f'installed venv interpreter imports an unexpected package: {root}')

    try:
        subprocess.run([str(python), '-I', '-m', 'ai_skills', '--help'], capture_output=True,
                       text=True, check=True, cwd=python.parents[1], env=_isolated_environment(), timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'installed runtime launcher unusable: {python}: {error}') from error


def _install_package(plan):
    # Isolated mode (-I) keeps the invoking checkout off sys.path; the temporary
    # official checkout stays the explicit pip build source.
    env, cwd = _isolated_environment(), None
    _run_child([str(plan.config.python), '-I', '-m', 'venv', str(plan.prefix)], cwd=plan.prefix, env=env)
    # Updates run pip from the candidate prefix with allowlisted settings so caller
    # destinations cannot write outside it. An ordinary install keeps the caller's
    # cwd and pip configuration, so relative constraints and find-links resolve.
    if plan.source_revision: env, cwd = _pip_environment(plan.python, env, plan.prefix), plan.prefix
    for command in plan.pip_commands: _run_child(command, cwd=cwd, env=env)
    try:
        result = subprocess.run([str(plan.python), '-I', '-m', 'pip', 'check'], cwd=cwd, env=env,
                                capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as cause:
        detail = '\n'.join(text.strip() for text in (cause.stdout, cause.stderr) if text and text.strip())
        error = ValueError(f'candidate dependency check failed for {", ".join(plan.dependencies)}: {detail}')
        error.add_note('Re-bootstrap with compatible target dependencies from a fresh official checkout; see README Existing installations.')
        raise error from cause
    if result.stdout: print(result.stdout, end='')
    return _runtime_root(plan.python)


def _validate_payload(root, prefix):
    root = Path(root)
    if not root.resolve().is_relative_to(prefix.resolve()): raise ValueError('installed package escapes its immutable runtime')
    required = [root / 'data' / name for name in RESOURCE_NAMES] + [root / 'skills' / name / 'SKILL.md' for name in SKILL_NAMES]
    required += sorted(root.rglob('*.py'))
    for path in required:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(prefix.resolve()): raise ValueError(f'missing or escaped installed payload: {path}')
    return {str(path.relative_to(prefix)): _hash(path) for path in required}


def _verify_payload(receipt, prefix):
    for relative, digest in receipt.get('payload_hashes', {}).items():
        path = prefix / relative
        if not path.resolve().is_relative_to(prefix.resolve()) or not path.is_file() or _hash(path) != digest:
            raise ValueError(f'installed runtime payload changed: {path}')










def _copy_tree(source, destination):
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source), target_is_directory=True)
    else:
        shutil.copytree(source, destination, symlinks=True)


def _register_copy(registration, root, fingerprint, transaction):
    target = Path(registration['target'])
    for ancestor in target.parents:
        if ancestor.is_symlink(): raise ValueError(f'symlink escape in registration path: {ancestor}')
    exists = target.exists() or target.is_symlink()
    if exists:
        if registration.get('existing_sha256') != _tree_hash(target):
            raise ValueError(f'skill registration changed after planning: {target}')
        backup = Path(registration['backup'])
        if backup.exists() or backup.is_symlink():
            if _tree_hash(backup) != registration['existing_sha256']:
                raise ValueError(f'registration backup differs from planned original: {backup}')
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            _copy_tree(target, backup)
    elif registration.get('existing_sha256'):
        raise ValueError(f'skill registration disappeared after planning: {target}')
    payload = (root / 'skills' / registration['skill'] / 'SKILL.md').read_text(encoding='utf-8')
    target.parent.mkdir(parents=True, exist_ok=True)
    token = transaction.state['token']
    staged = target.with_name('.' + target.name + '.staging.' + token)
    try:
        staged.mkdir()
        (staged / 'SKILL.md').write_text(payload, encoding='utf-8')
        write_json_atomic(staged / '.ai-skills-registration.json', {'owner': OWNER, 'path': str(target), 'fingerprint': fingerprint, 'files': {'SKILL.md': _hash(staged / 'SKILL.md')}})
    except BaseException:
        # Before expect(), this token's directory is scratch; the target is untouched.
        try:
            if staged.exists() or staged.is_symlink(): _remove_tree(staged)
        except OSError: pass  # Journal recovery retries cleanup without masking the failure.
        raise
    transaction.expect(target, _tree_hash(staged))
    if exists: target.rename(target.with_name('.' + target.name + '.previous.' + token))
    staged.rename(target)  # The journal owns cleanup and restoration after expect().


def _restore_copy(registration, fingerprint):
    target = Path(registration['target'])
    receipt = _registration_receipt(target)
    if receipt['fingerprint'] != fingerprint: raise ValueError(f'registration ownership changed: {target}')
    backup = Path(registration['backup']) if registration.get('backup') else None
    if backup is not None:
        if not (backup.exists() or backup.is_symlink()) or _tree_hash(backup) != registration['existing_sha256']:
            raise ValueError(f'original registration backup changed: {backup}')
    shutil.rmtree(target)
    if backup is not None: _copy_tree(backup, target)


def _adapter_state(target):
    """Digest of an adapter target, or None when absent; never through a symlinked ancestor."""
    target = Path(target)
    for ancestor in target.parents:
        if ancestor.is_symlink(): raise ValueError(f'symlink escape in adapter registration: {ancestor}')
    return _tree_hash(target) if target.exists() or target.is_symlink() else None


def _remove_tree(target):
    if target.is_symlink() or target.is_file(): target.unlink()
    else: shutil.rmtree(target)


def _restore_backup(item):
    backup, target = Path(item['backup']), Path(item['target'])
    if _tree_hash(backup) != item['existing_sha256']: raise ValueError(f'adapter backup changed: {backup}')
    target.parent.mkdir(parents=True, exist_ok=True); _copy_tree(backup, target)


_BASELINE_KEYS = ('existing_sha256', 'backup', 'original_backup', 'original_sha256')


class _AdapterActivation:
    """Expected state of every adapter target during one activation.

    A target shared by several adapters (``~/.agents/skills``) is a single
    transition from its pre-activation state: the first adapter backs it up and
    later adapters inherit that baseline, so rollback and uninstall restore it once.
    """

    def __init__(self, transaction):
        self.current, self.owners = {}, {}  # target -> digest written here | items sharing it
        self.transaction = transaction

    def expected(self, item):
        target = item['target']
        return self.current[target] if target in self.current else item.get('existing_sha256')

    def check(self, registration):
        """Refuse a target appearing, vanishing or changing outside this activation."""
        for item in registration.get('paths', []):
            if _adapter_state(item['target']) != self.expected(item):
                raise ValueError(f'adapter registration changed after planning: {item["target"]}')

    def prepare(self, registration):
        self.check(registration)
        for item in registration.get('paths', []):
            owners = self.owners.setdefault(item['target'], [])
            if owners:
                for key in _BASELINE_KEYS:
                    if key in owners[0]: item[key] = owners[0][key]
                    else: item.pop(key, None)
            elif item.get('backup'): self._backup(item)
            owners.append(item)

    @staticmethod
    def _backup(item):
        backup = Path(item['backup'])
        if backup.exists() or backup.is_symlink():
            if _tree_hash(backup) != item['existing_sha256']: raise ValueError(f'adapter backup changed: {backup}')
        else:
            backup.parent.mkdir(parents=True, exist_ok=True); _copy_tree(Path(item['target']), backup)

    def settle(self, registration):
        """Expect each target as observed after this activation's own undo succeeded."""
        for item in registration.get('paths', []):
            self.current[item['target']] = _adapter_state(item['target'])
        self.transaction.settle(registration)

    def record(self, registration):
        for item in registration.get('paths', []):
            self.settle_owners(item['target'], self.owners[item['target']], _adapter_state(item['target']))
        self.transaction.settle(registration)

    def settle_owners(self, target, owners, digest):
        self.current[target] = digest
        for owner in owners:
            if digest: owner['installed_sha256'] = digest
            else: owner.pop('installed_sha256', None)

    def share(self, retained):
        """Give retained owners of a rewritten shared target its new receipt and baseline."""
        for registration in retained:
            for item in registration.get('paths', []):
                owners = self.owners.get(item['target'])
                if not owners: continue
                for key in _BASELINE_KEYS:
                    if key in owners[0]: item[key] = owners[0][key]
                    else: item.pop(key, None)
                self.settle_owners(item['target'], [item], self.current.get(item['target']))


def _restore_adapter(registration):
    for item in registration.get('paths', []):
        target = Path(item['target'])
        if target.exists() or target.is_symlink():
            digest = _tree_hash(target)
            # A target shared with another adapter may already hold its restored baseline.
            if item.get('backup') and digest == item.get('existing_sha256'): continue
            expected = item.get('installed_sha256') or item.get('existing_sha256')
            if not expected or digest != expected:
                raise ValueError(f'uncommitted or edited adapter content preserved; inspect its original backup: {target}')
            _remove_tree(target)
        if item.get('backup'): _restore_backup(item)


def _registration_payload(prefix, root, version):
    base = prefix / 'registration'
    skills = base / 'skills' / 'skills'
    for name in SKILL_NAMES:
        target = skills / name; target.mkdir(parents=True, exist_ok=True)
        text = (root / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')
        text = re.sub(r'(?m)^name:\s*.*$', 'name: ' + name, text, count=1)
        (target / 'SKILL.md').write_text(text, encoding='utf-8')
    gemini = base / 'gemini'; gemini.mkdir(parents=True, exist_ok=True)
    write_json_atomic(gemini / 'gemini-extension.json', {'name': 'ai-skill-collections', 'version': version, 'description': 'Versioned AI-Skills workflows'})
    shutil.copytree(skills, gemini / 'skills', dirs_exist_ok=True)


# Caller npm settings that would point npx at an invoking project's local bins.
_NPM_PROJECT_KEYS = frozenset({'NPM_CONFIG_PREFIX', 'NPM_CONFIG_LOCAL_PREFIX'})


def _run_registration(argv, *, home=None):
    # argv lists only; never shell interpolation or automatic tool installs.
    # A fresh empty cwd keeps npx from resolving the invoking checkout's
    # node_modules/.bin or project .npmrc instead of the expected package.
    from ai_skills.runner import resolve_executable
    environment = {key: value for key, value in os.environ.items() if key.upper() not in _NPM_PROJECT_KEYS}
    if home is not None:
        selected = str(_absolute(home))
        environment.update(HOME=selected, USERPROFILE=selected)
        if os.name == 'nt':
            drive, tail = os.path.splitdrive(selected)
            environment.update(HOMEDRIVE=drive, HOMEPATH=tail)
    with tempfile.TemporaryDirectory(prefix='ai-skills-adapter-') as cwd:
        for key in list(environment):
            if key.upper() in ('NPM_CONFIG_CACHE', 'NPM_CONFIG_LOGS_DIR'): environment.pop(key)
        environment['npm_config_cache'] = str(Path(cwd) / 'npm-cache')
        environment['npm_config_logs_dir'] = str(Path(cwd) / 'npm-logs')
        _run_child(resolve_executable(tuple(argv), environment), cwd=cwd, env=environment)


def _clean_interrupted(prefix):
    marker = prefix / '.installing.json'
    if not marker.is_file(): raise ValueError(f'unowned incomplete runtime directory: {prefix}')
    owner = _object(marker)
    if owner.get('owner') != OWNER or owner.get('prefix') != str(prefix) or not DirectoryLock._proven_dead(owner):
        raise ValueError(f'incomplete runtime has a live or unknown owner: {prefix}')
    for child in prefix.rglob('*'):
        if (child.is_symlink() and not child.resolve().is_relative_to(prefix.resolve())
                and not re.fullmatch(r'python(?:3(?:\.\d+)?)?', child.name)):
            raise ValueError(f'escaped interrupted staging directory: {child}')
    shutil.rmtree(prefix)


