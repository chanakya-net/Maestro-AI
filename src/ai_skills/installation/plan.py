"""Read-only installation planning, source inspection, and manifest queries."""
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

OWNER = 'ai-skills-python-runtime/1'
PROFILES = {'codex': 'codex', 'copilot': 'github-copilot', 'antigravity': 'antigravity', 'agy': 'agy'}
AGENTS = ('claude', 'gemini', 'codex', 'copilot', 'antigravity', 'agy')


def _absolute(path):
    # Resolving the python symlink loses its venv identity. Keep this lexical.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def default_assets_dest():
    configured = os.environ.get('ASSETS_DEST')
    return _absolute(configured or Path.home() / '.ai-skill-collections/assets')


def _python(prefix):
    return prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _object(path):
    value = load_json(path)
    if not isinstance(value, dict): raise ValueError(f'expected JSON object: {path}')
    return value


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''): digest.update(chunk)
    return digest.hexdigest()


def _safe_assets(path, home):
    path = _absolute(path)
    home = _absolute(home)
    if path == Path(path.anchor) or path == home or path in home.parents:
        raise ValueError(f'unsafe assets destination: {path}')
    # Reject any existing symlink ancestor, including a path that currently
    # traverses through one into somebody else's installation.
    for candidate in (path, *path.parents):
        if candidate.is_symlink(): raise ValueError(f'symlink escape in assets destination: {candidate}')
    return path


def _source(source):
    if source is None:
        checkout = Path(__file__).resolve().parents[2]
        if (checkout / 'pyproject.toml').is_file(): source = checkout
        else: raise ValueError('installed package requires --source <wheel-or-source-directory> to create an immutable runtime')
    source = _absolute(source)
    digest = hashlib.sha256()
    if source.is_file() and source.suffix == '.whl':
        with zipfile.ZipFile(source) as archive:
            candidates = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(candidates) != 1: raise ValueError('wheel must contain exactly one distribution metadata record')
            info = email.message_from_bytes(archive.read(candidates[0]))
            if info['Name'].lower().replace('_', '-') != 'ai-skills': raise ValueError('source wheel is not ai-skills')
            version = info['Version']; dependencies = info.get_all('Requires-Dist', [])
            required = ['ai_skills/data/' + name for name in RESOURCE_NAMES] + ['ai_skills/skills/' + name + '/SKILL.md' for name in SKILL_NAMES]
            missing = [name for name in required if name not in archive.namelist()]
            if missing: raise ValueError('wheel is missing maintained resources: ' + ', '.join(missing))
        digest.update(source.read_bytes())
    elif source.is_dir() and (source / 'pyproject.toml').is_file():
        info = tomllib.loads((source / 'pyproject.toml').read_text(encoding='utf-8')).get('project', {})
        if info.get('name', '').lower().replace('_', '-') != 'ai-skills': raise ValueError('source project is not ai-skills')
        version = info['version']; dependencies = info.get('dependencies', [])
        paths = [source / 'pyproject.toml']
        paths += sorted((source / 'src/ai_skills').rglob('*.py'))
        paths += [source / 'assets' / name for name in RESOURCE_NAMES]
        paths += [source / 'skills' / name / 'SKILL.md' for name in SKILL_NAMES]
        for path in paths:
            if not path.is_file(): raise ValueError(f'missing maintained source file: {path}')
            digest.update(str(path.relative_to(source)).replace(os.sep, '/').encode())
            digest.update(path.read_bytes())
    else:
        raise ValueError(f'--source must be an ai-skills wheel or source directory: {source}')
    if not version or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version): raise ValueError('invalid package version')
    return source, version, tuple(dependencies), digest.hexdigest()


def _dependency_pins(dependencies):
    """Pin the dependencies of the already installed invoking distribution.

    The public runtime has only psutil and platform-qualified pywin32. A new
    requirement must already be installed before it can form an immutable
    offline-reviewable plan; pip never silently resolves a different version.
    """
    pins = []
    for requirement in dependencies:
        if ';' in requirement:
            requirement, marker = requirement.split(';', 1)
            marker = marker.strip().replace('"', "'")
            if re.fullmatch(r"extra\s*==\s*'[^']+'", marker): continue
            match = re.fullmatch(r"sys_platform\s*==\s*'([^']+)'", marker)
            if not match: raise ValueError(f'unsupported dependency marker; install a matching wheel first: {marker}')
            if sys.platform != match.group(1): continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*(.*)', requirement.strip())
        if not match: raise ValueError(f'invalid dependency requirement: {requirement}')
        name = match.group(1)
        try: version = metadata.version(name)
        except metadata.PackageNotFoundError as cause:
            error = ValueError(f'incompatible dependency {requirement}: not installed in the invoking runtime')
            error.add_note('Re-bootstrap from a fresh official checkout with its dependencies; see README Existing installations.')
            raise error from cause
        try:
            if not _satisfies(version, match.group(2)):
                raise ValueError(f'incompatible dependency {requirement}: installed version {version}')
        except ValueError as error:
            error.add_note(f'Target requirement {requirement}; installed invoking version {version}.')
            error.add_note('Re-bootstrap from a fresh official checkout with compatible dependencies; see README Existing installations.')
            raise
        pins.append(name + '==' + version)
    return tuple(sorted(pins))


@dataclass(frozen=True)
class InstallConfig:
    source: Path | None = None
    assets_dest: Path | None = None
    agents: tuple[str, ...] = ()
    home: Path | None = None
    python: Path = field(default_factory=lambda: Path(sys.executable))
    dry_run: bool = False
    replace_existing: bool = False
    source_revision: dict | None = None
    skip_current_revision: bool = False
    refresh_registrations: bool = False


@dataclass(frozen=True)
class InstallationPlan:
    config: InstallConfig
    source: Path
    assets_dest: Path
    prefix: Path
    python: Path
    version: str
    fingerprint: str
    payload_fingerprint: str
    dependencies: tuple[str, ...]
    pip_commands: tuple[tuple[str, ...], ...]
    registrations: tuple[dict, ...]
    source_revision: dict | None = None
    active_snapshot: bytes | None = None

    def to_record(self):
        record = {'source': str(self.source), 'assets_dest': str(self.assets_dest), 'prefix': str(self.prefix),
                'python': str(self.python), 'version': self.version, 'fingerprint': self.fingerprint,
                'dependencies': list(self.dependencies), 'pip_commands': [list(c) for c in self.pip_commands],
                'registrations': list(self.registrations)}
        if self.source_revision: record['source_revision'] = dict(self.source_revision)
        return record


def _check_source_revision(revision):
    """Validate the optional official-source record before anything is written."""
    if revision is None: return None
    if (not isinstance(revision, dict) or not isinstance(revision.get('commit'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', revision['commit'])
            or not all(isinstance(revision.get(k), str) and revision[k] for k in ('repository', 'branch'))):
        raise ValueError('invalid source revision: expected repository, branch and 40-hex commit')
    return {k: revision[k] for k in ('repository', 'branch', 'commit')}


def _detect(home):
    commands = {'claude': 'claude', 'gemini': 'gemini', 'codex': 'codex', 'copilot': 'gh', 'agy': 'agy'}
    return tuple(agent for agent in AGENTS if (
        (home / '.antigravity').is_dir() if agent == 'antigravity'
        else bool(shutil.which(commands[agent]))))


def _external_paths(agent, home):
    if agent == 'gemini': return [home / '.gemini/extensions/ai-skill-collections']
    roots = [home / '.agents/skills']
    if agent == 'copilot': roots.append(home / '.copilot/skills')
    elif agent == 'antigravity': roots.append(home / '.gemini/antigravity/skills')
    elif agent == 'agy': roots.append(home / '.agy/skills')
    return [root / name for root in roots for name in SKILL_NAMES]


def _carry_original(entry, prior):
    """Keep the pre-Maestro uninstall baseline when replacing managed content.

    ``backup`` stays the immediate prior registration for activation rollback;
    ``original_backup`` (None when nothing preceded Maestro) is what uninstall restores.
    """
    if prior is None: return
    keys = ('original_backup', 'original_sha256') if 'original_backup' in prior else ('backup', 'existing_sha256')
    entry['original_backup'], entry['original_sha256'] = (prior.get(key) for key in keys)


def _backup_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _transition_backup(prefix, agent, name, digest, stamp=None):
    """Rollback snapshot of one prior registration state for this candidate.

    Keyed by the prior content digest so reactivating a retained prefix after a
    different registration was active gets its own snapshot instead of colliding
    with the one taken on that prefix's first activation.
    """
    if stamp: name += '.edited-' + stamp
    return str(prefix / 'registration-backups' / agent / (name + '.' + digest))


def _uninstall_view(entry):
    if 'original_backup' not in entry: return entry
    return dict(entry, backup=entry['original_backup'], existing_sha256=entry['original_sha256'])


def _owned_backup(backup, destination):
    """An uninstall backup must live in an owned retained runtime's backup directory."""
    backup, runtimes = _absolute(backup), destination / 'runtimes'
    if not backup.is_relative_to(runtimes) or len(backup.relative_to(runtimes).parts) < 2: return False
    prefix = runtimes / backup.relative_to(runtimes).parts[0]
    if not backup.is_relative_to(prefix / 'registration-backups'): return False
    for ancestor in (backup, *backup.parents):
        if ancestor == runtimes: break
        if ancestor.is_symlink() and ancestor != backup: return False
    _owned_receipt(prefix)
    return True


def _manifest_registrations(manifest, destination, home):
    """Validate the manifest authority before deriving automatic update targets."""
    if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise ValueError('unsupported active runtime manifest schema')
    def check_runtime(prefix, fingerprint):
        if not isinstance(prefix, str) or not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise ValueError('invalid registration runtime reference')
        path = _absolute(prefix)
        if str(path) != prefix or path.parent != destination / 'runtimes' or path.is_symlink() or path.parent.is_symlink():
            raise ValueError('registration runtime prefix escapes owned runtimes')
        _owned_receipt(path, fingerprint)
    check_runtime(manifest.get('prefix'), manifest.get('fingerprint'))
    entries = manifest.get('registrations', [])
    if not isinstance(entries, list): raise ValueError('invalid manifest registrations')
    selected, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict): raise ValueError('invalid manifest registration')
        agent, kind = entry.get('agent'), entry.get('kind')
        if not isinstance(agent, str) or agent not in AGENTS or kind != ('copy' if agent in ('codex', 'claude') else 'command'):
            raise ValueError('invalid manifest registration identity')
        check_runtime(entry.get('runtime_prefix', manifest['prefix']), entry.get('fingerprint', manifest['fingerprint']))
        if kind == 'copy':
            skill = entry.get('skill')
            if not isinstance(skill, str) or skill not in SKILL_NAMES or entry.get('target') != str(home / ('.codex' if agent == 'codex' else '.claude') / 'skills' / skill):
                raise ValueError('invalid manifest copy target')
            key = (agent, skill)
        else:
            key = (agent, None)
            paths = entry.get('paths', [])
            allowed = {str(path) for path in _external_paths(agent, home)}
            if not isinstance(paths, list) or any(not isinstance(item, dict) or item.get('target') not in allowed for item in paths):
                raise ValueError('invalid manifest adapter target')
        if key in seen: raise ValueError('duplicate manifest registration')
        seen.add(key); selected.append(key)
    return selected


def _active_manifest(destination, home, *, automatic):
    """Read owned state without fetching source or changing the installation."""
    active = destination / 'runtime.json'
    if automatic and (active.is_symlink() or not active.is_file()):
        raise ValueError('no owned active runtime manifest; install first')
    snapshot = active.read_bytes() if active.exists() else None
    previous = json.loads(snapshot) if snapshot is not None else None
    if snapshot is not None:
        if not isinstance(previous, dict): raise ValueError(f'expected JSON object: {active}')
        if previous.get('owner') != OWNER: raise ValueError('active runtime manifest is unowned')
    selected = _manifest_registrations(previous, destination, home) if automatic else None
    return previous, snapshot, selected


def plan_install(config: InstallConfig) -> InstallationPlan:
    if sys.version_info < (3, 12): raise ValueError('Python 3.12 or newer is required')
    revision = _check_source_revision(config.source_revision)
    home = _absolute(config.home or Path.home())
    destination = _safe_assets(config.assets_dest or default_assets_dest(), home)
    _require_no_transaction(destination)
    source, version, dependencies, payload = _source(config.source)
    pins = _dependency_pins(dependencies)
    identity = {'payload': payload, 'dependencies': pins, 'python': sys.version,
                'platform': sys.platform, 'machine': platform.machine()}
    if revision: identity['source_revision'] = revision
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    prefix = destination / 'runtimes' / (version + '-' + fingerprint[:20])
    for candidate in (prefix, prefix.parent):
        if candidate.is_symlink(): raise ValueError(f'symlink escape in runtime directory: {candidate}')
    python = _python(prefix)
    pip_commands = []
    if pins: pip_commands.append((str(python), '-I', '-m', 'pip', 'install', *pins))
    pip_commands.append((str(python), '-I', '-m', 'pip', 'install', '--no-deps', str(source)))
    automatic = revision is not None and not config.agents
    previous, snapshot, selected = _active_manifest(destination, home, automatic=automatic)
    agents = tuple(dict.fromkeys(agent for agent, _ in selected)) if automatic else (config.agents or _detect(home))
    registrations = []
    for agent in dict.fromkeys(agents):
        if agent not in AGENTS: raise ValueError(f'unsupported registration agent: {agent}')
        if agent in ('codex', 'claude'):
            root = home / ('.codex' if agent == 'codex' else '.claude') / 'skills'
            for skill in SKILL_NAMES:
                if automatic and (agent, skill) not in selected: continue
                registrations.append({'kind': 'copy', 'agent': agent, 'skill': skill, 'target': str(root / skill)})
        elif agent == 'gemini':
            registrations.append({'kind': 'command', 'agent': agent, 'argv': ['gemini', 'extensions', 'install', '--consent', str(prefix / 'registration/gemini')],
                                  'undo': ['gemini', 'extensions', 'uninstall', 'ai-skill-collections']})
        else:
            registrations.append({'kind': 'command', 'agent': agent,
                                  'argv': ['npx', '-y', 'skills', 'add', str(prefix / 'registration/skills'), '-a', PROFILES[agent], '--yes', '--global'],
                                  'undo': ['npx', '-y', 'skills', 'remove', *(s for s in SKILL_NAMES), '-a', PROFILES[agent], '--yes', '--global']})
    external_owned, prior_entries = {}, {}
    if previous is not None:
        for r in previous.get('registrations', []):
            if r.get('kind') == 'copy': prior_entries[r['target']] = r
            for p in r.get('paths', []):
                prior_entries[p['target']] = p
                if p.get('installed_sha256'): external_owned[p['target']] = p['installed_sha256']
    stamp = _backup_stamp()
    for registration in registrations:
        if registration['kind'] != 'copy':
            registration['paths'] = []
            for target in _external_paths(registration['agent'], home):
                for ancestor in target.parents:
                    if ancestor.is_symlink(): raise ValueError(f'symlink escape in adapter registration: {ancestor}')
                item = {'target': str(target)}
                if target.exists() or target.is_symlink():
                    digest = _tree_hash(target)
                    managed = str(target) in external_owned
                    edited = managed and external_owned[str(target)] != digest
                    if not config.replace_existing and (not managed or (edited and _contains_symlink(target))):
                        raise ValueError(f'unowned adapter registration already exists: {target}; use --dry-run --replace-existing to review its backup')
                    item.update(existing_sha256=digest, backup=_transition_backup(prefix, registration['agent'], hashlib.sha256(str(target).encode()).hexdigest(), digest, stamp if edited else None))
                    if edited: item['existing_edited'] = True
                    if managed: _carry_original(item, prior_entries.get(str(target)))
                else: _carry_original(item, prior_entries.get(str(target)))  # recreating a missing managed target
                registration['paths'].append(item)
            continue
        target = Path(registration['target'])
        for ancestor in target.parents:
            if ancestor.is_symlink(): raise ValueError(f'symlink escape in registration path: {ancestor}')
        if target.exists() or target.is_symlink():
            owned = edited = False
            try:
                _registration_receipt(target); owned = True
            except ValueError:
                try:
                    _managed_receipt(target); edited = True
                except ValueError:
                    if not config.replace_existing:
                        raise ValueError(f'unowned skill registration already exists: {target}; review --dry-run --replace-existing to back it up explicitly')
            digest = _tree_hash(target)
            registration.update(backup=_transition_backup(prefix, registration['agent'], registration['skill'], digest, stamp if edited else None),
                                existing_sha256=digest, existing_owned=owned or edited)
            if edited: registration['existing_edited'] = True
            if owned or edited: _carry_original(registration, prior_entries.get(str(target)))
        else: _carry_original(registration, prior_entries.get(str(target)))  # recreating a missing managed target
    return InstallationPlan(config, source, destination, prefix, python, version, fingerprint, payload, pins, tuple(pip_commands), tuple(registrations), revision, snapshot)




def _receipt(prefix):
    return prefix / '.ai-skills-runtime.json'


def _owned_receipt(prefix, fingerprint=None):
    path = _receipt(prefix)
    if prefix.is_symlink() or path.is_symlink() or not path.is_file(): raise ValueError(f'unowned runtime directory: {prefix}')
    receipt = _object(path)
    if receipt.get('owner') != OWNER or receipt.get('prefix') != str(prefix) or fingerprint and receipt.get('fingerprint') != fingerprint:
        raise ValueError(f'ownership or fingerprint mismatch: {prefix}')
    return receipt


def _managed_receipt(path):
    receipt_path = path / '.ai-skills-registration.json'
    if path.is_symlink() or not receipt_path.is_file(): raise ValueError(f'unowned or symlinked skill registration: {path}')
    receipt = _object(receipt_path)
    if receipt.get('owner') != OWNER or receipt.get('path') != str(path): raise ValueError(f'unowned skill registration: {path}')
    return receipt


def _registration_receipt(path):
    receipt = _managed_receipt(path)
    files = receipt.get('files', {})
    if set(files) != {'SKILL.md'} or {p.name for p in path.iterdir()} != {'SKILL.md', '.ai-skills-registration.json'}:
        raise ValueError(f'unowned additional content in skill registration: {path}')
    for name, digest in files.items():
        child = path / name
        if not child.resolve().is_relative_to(path.resolve()) or not child.is_file() or _hash(child) != digest: raise ValueError(f'edited or escaped skill registration: {child}')
    return receipt


def _contains_symlink(path):
    if path.is_symlink(): return True
    return path.is_dir() and any(_contains_symlink(child) for child in path.iterdir())


def _tree_hash(path):
    path = Path(path)
    digest = hashlib.sha256()
    def add(item, relative):
        digest.update(relative.encode('utf-8'))
        if item.is_symlink():
            digest.update(b'link:' + os.readlink(item).encode('utf-8'))
        elif item.is_dir():
            digest.update(b'directory:')
            for child in sorted(item.iterdir()): add(child, relative + '/' + child.name)
        elif item.is_file(): digest.update(b'file:' + item.read_bytes())
        else: raise ValueError(f'unsupported registration content: {item}')
    add(path, '.')
    return digest.hexdigest()


def _transaction_path(destination):
    return destination / '.installation-transaction.json'


def _require_no_transaction(destination):
    path = _transaction_path(destination)
    if path.exists() or path.is_symlink():
        raise ValueError(f'pending installation recovery: {path}; a mutating retry is required')


