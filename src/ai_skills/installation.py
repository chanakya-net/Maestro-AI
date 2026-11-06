"""Plan and install immutable native Python runtimes and owned registrations.

Plans perform read-only discovery. Only apply functions create environments,
run pip/provider registration, activate a manifest, or remove owned files.
Global Python/Node installations and provider permission settings are untouched.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
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
import tomllib
import uuid
import zipfile

import psutil

from ai_skills.resources import RESOURCE_NAMES, SKILL_NAMES
from ai_skills.storage import DirectoryLock, load_json, write_json_atomic

OWNER = 'ai-skills-python-runtime/1'
PROFILES = {'codex': 'codex', 'copilot': 'github-copilot', 'antigravity': 'antigravity', 'agy': 'agy'}
AGENTS = ('claude', 'gemini', 'codex', 'copilot', 'antigravity', 'agy')


def _absolute(path):
    # Resolving the python symlink loses its venv identity. Keep this lexical.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def default_assets_dest():
    return _absolute(os.environ.get('ASSETS_DEST', str(Path.home() / '.ai-skill-collections/assets')))


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
        match = re.match(r'([A-Za-z0-9_.-]+)', requirement.strip())
        if not match: raise ValueError(f'invalid dependency requirement: {requirement}')
        name = match.group(1)
        try: version = metadata.version(name)
        except metadata.PackageNotFoundError as error: raise ValueError(f'dependency {name} must be installed in the invoking runtime before creating a pinned installation') from error
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


def plan_install(config: InstallConfig) -> InstallationPlan:
    if sys.version_info < (3, 12): raise ValueError('Python 3.12 or newer is required')
    revision = _check_source_revision(config.source_revision)
    home = _absolute(config.home or Path.home())
    destination = _safe_assets(config.assets_dest or default_assets_dest(), home)
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
    if pins: pip_commands.append((str(python), '-m', 'pip', 'install', *pins))
    pip_commands.append((str(python), '-m', 'pip', 'install', '--no-deps', str(source)))
    agents = config.agents or _detect(home)
    registrations = []
    for agent in dict.fromkeys(agents):
        if agent not in AGENTS: raise ValueError(f'unsupported registration agent: {agent}')
        if agent in ('codex', 'claude'):
            root = home / ('.codex' if agent == 'codex' else '.claude') / 'skills'
            for skill in SKILL_NAMES:
                registrations.append({'kind': 'copy', 'agent': agent, 'skill': skill, 'target': str(root / skill)})
        elif agent == 'gemini':
            registrations.append({'kind': 'command', 'agent': agent, 'argv': ['gemini', 'extensions', 'install', '--consent', str(prefix / 'registration/gemini')],
                                  'undo': ['gemini', 'extensions', 'uninstall', 'ai-skill-collections']})
        else:
            registrations.append({'kind': 'command', 'agent': agent,
                                  'argv': ['npx', '-y', 'skills', 'add', str(prefix / 'registration/skills'), '-a', PROFILES[agent], '--yes', '--global'],
                                  'undo': ['npx', '-y', 'skills', 'remove', *(s for s in SKILL_NAMES), '-a', PROFILES[agent], '--yes', '--global']})
    active = destination / 'runtime.json'
    external_owned = {}
    if active.exists():
        previous = _object(active)
        if previous.get('owner') != OWNER: raise ValueError('active runtime manifest is unowned')
        for r in previous.get('registrations', []):
            for p in r.get('paths', []):
                if p.get('installed_sha256'): external_owned[p['target']] = p['installed_sha256']
    for registration in registrations:
        if registration['kind'] != 'copy':
            registration['paths'] = []
            for target in _external_paths(registration['agent'], home):
                for ancestor in target.parents:
                    if ancestor.is_symlink(): raise ValueError(f'symlink escape in adapter registration: {ancestor}')
                item = {'target': str(target)}
                if target.exists() or target.is_symlink():
                    digest = _tree_hash(target)
                    if external_owned.get(str(target)) != digest and not config.replace_existing:
                        raise ValueError(f'unowned adapter registration already exists: {target}; use --dry-run --replace-existing to review its backup')
                    item.update(existing_sha256=digest, backup=str(prefix / 'registration-backups' / registration['agent'] / hashlib.sha256(str(target).encode()).hexdigest()))
                registration['paths'].append(item)
            continue
        target = Path(registration['target'])
        for ancestor in target.parents:
            if ancestor.is_symlink(): raise ValueError(f'symlink escape in registration path: {ancestor}')
        if target.exists() or target.is_symlink():
            owned = False
            try:
                _registration_receipt(target); owned = True
            except ValueError:
                if not config.replace_existing:
                    raise ValueError(f'unowned skill registration already exists: {target}; review --dry-run --replace-existing to back it up explicitly')
            registration.update(backup=str(prefix / 'registration-backups' / registration['agent'] / registration['skill']),
                                existing_sha256=_tree_hash(target), existing_owned=owned)
    return InstallationPlan(config, source, destination, prefix, python, version, fingerprint, payload, pins, tuple(pip_commands), tuple(registrations), revision)


def _receipt(prefix):
    return prefix / '.ai-skills-runtime.json'


def _owned_receipt(prefix, fingerprint=None):
    path = _receipt(prefix)
    if prefix.is_symlink() or path.is_symlink() or not path.is_file(): raise ValueError(f'unowned runtime directory: {prefix}')
    receipt = _object(path)
    if receipt.get('owner') != OWNER or receipt.get('prefix') != str(prefix) or fingerprint and receipt.get('fingerprint') != fingerprint:
        raise ValueError(f'ownership or fingerprint mismatch: {prefix}')
    return receipt


def _runtime_root(python):
    code = 'import ai_skills,json;from pathlib import Path;print(json.dumps(str(Path(ai_skills.__file__).parent)))'
    result = subprocess.run([str(python), '-c', code], capture_output=True, text=True, check=True)
    return Path(json.loads(result.stdout))


def _install_package(plan):
    subprocess.run([str(plan.config.python), '-m', 'venv', str(plan.prefix)], check=True, stdout=sys.stdout)
    for command in plan.pip_commands: subprocess.run(command, check=True, stdout=sys.stdout)
    subprocess.run([str(plan.python), '-m', 'pip', 'check'], check=True, stdout=sys.stdout)
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


def _registration_receipt(path):
    receipt_path = path / '.ai-skills-registration.json'
    if path.is_symlink() or not receipt_path.is_file(): raise ValueError(f'unowned or symlinked skill registration: {path}')
    receipt = _object(receipt_path)
    if receipt.get('owner') != OWNER or receipt.get('path') != str(path): raise ValueError(f'unowned skill registration: {path}')
    files = receipt.get('files', {})
    if set(files) != {'SKILL.md'} or {p.name for p in path.iterdir()} != {'SKILL.md', '.ai-skills-registration.json'}:
        raise ValueError(f'unowned additional content in skill registration: {path}')
    for name, digest in files.items():
        child = path / name
        if not child.resolve().is_relative_to(path.resolve()) or not child.is_file() or _hash(child) != digest: raise ValueError(f'edited or escaped skill registration: {child}')
    return receipt


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


def _copy_tree(source, destination):
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source), target_is_directory=True)
    else:
        shutil.copytree(source, destination, symlinks=True)


def _register_copy(registration, root, fingerprint):
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
    staged = target.with_name('.' + target.name + '.staging.' + uuid.uuid4().hex)
    staged.mkdir()
    (staged / 'SKILL.md').write_text(payload, encoding='utf-8')
    write_json_atomic(staged / '.ai-skills-registration.json', {'owner': OWNER, 'path': str(target), 'fingerprint': fingerprint, 'files': {'SKILL.md': _hash(staged / 'SKILL.md')}})
    previous = None
    try:
        if exists:
            previous = target.with_name('.' + target.name + '.previous.' + uuid.uuid4().hex)
            target.rename(previous)
        staged.rename(target)
        if previous:
            if previous.is_symlink(): previous.unlink()
            else: shutil.rmtree(previous)
    except BaseException:
        if previous and (previous.exists() or previous.is_symlink()) and not target.exists(): previous.rename(target)
        raise
    finally:
        if staged.exists(): shutil.rmtree(staged)


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


def _backup_adapter(registration):
    for item in registration.get('paths', []):
        if not item.get('backup'): continue
        target, backup = Path(item['target']), Path(item['backup'])
        if _tree_hash(target) != item['existing_sha256']: raise ValueError(f'adapter registration changed after planning: {target}')
        if backup.exists() or backup.is_symlink():
            if _tree_hash(backup) != item['existing_sha256']: raise ValueError(f'adapter backup changed: {backup}')
        else:
            backup.parent.mkdir(parents=True, exist_ok=True); _copy_tree(target, backup)


def _restore_adapter(registration):
    for item in registration.get('paths', []):
        target = Path(item['target'])
        if target.exists() or target.is_symlink():
            expected = item.get('installed_sha256') or item.get('existing_sha256')
            if not expected or _tree_hash(target) != expected:
                raise ValueError(f'uncommitted or edited adapter content preserved; inspect its original backup: {target}')
            if target.is_symlink(): target.unlink()
            else: shutil.rmtree(target)
        if item.get('backup'):
            backup = Path(item['backup'])
            if _tree_hash(backup) != item['existing_sha256']: raise ValueError(f'adapter backup changed: {backup}')
            target.parent.mkdir(parents=True, exist_ok=True); _copy_tree(backup, target)


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


def _run_registration(argv, *, home=None):
    # argv lists only; never shell interpolation or automatic tool installs.
    from ai_skills.runner import resolve_executable
    environment = dict(os.environ)
    if home is not None:
        selected = str(_absolute(home))
        environment.update(HOME=selected, USERPROFILE=selected)
        if os.name == 'nt':
            drive, tail = os.path.splitdrive(selected)
            environment.update(HOMEDRIVE=drive, HOMEPATH=tail)
    subprocess.run(resolve_executable(tuple(argv), environment), check=True, env=environment, stdout=sys.stdout)


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


def apply_install(plan: InstallationPlan, *, package_installer=None, registrar=None):
    if plan.config.dry_run: return plan.to_record()
    register = registrar or (lambda argv: _run_registration(argv, home=plan.config.home or Path.home()))
    source, version, dependencies, payload = _source(plan.source)
    if payload != plan.payload_fingerprint or _dependency_pins(dependencies) != plan.dependencies:
        raise ValueError('installation source or dependency versions changed after planning')
    _safe_assets(plan.assets_dest, plan.config.home or Path.home())
    plan.assets_dest.mkdir(parents=True, exist_ok=True)
    with DirectoryLock(plan.assets_dest / '.installation', timeout_seconds=.1):
        if plan.prefix.exists() and not _receipt(plan.prefix).exists(): _clean_interrupted(plan.prefix)
        if plan.prefix.exists():
            receipt = _owned_receipt(plan.prefix, plan.fingerprint); _verify_payload(receipt, plan.prefix)
            if plan.source_revision and receipt.get('source_revision') != plan.source_revision:
                raise ValueError('retained runtime source revision mismatch')
            if not plan.python.is_file(): raise ValueError(f'installed venv interpreter missing: {plan.python}')
            root = Path(receipt['package_root'])
        else:
            plan.prefix.mkdir(parents=True)
            marker = {'owner': OWNER, 'prefix': str(plan.prefix), 'host': socket.gethostname(), 'pid': os.getpid(), 'create_time': psutil.Process().create_time(), 'token': uuid.uuid4().hex}
            write_json_atomic(plan.prefix / '.installing.json', marker)
            try:
                root = Path((package_installer or _install_package)(plan))
                if _source(plan.source)[3] != plan.payload_fingerprint:
                    raise ValueError('installation source changed while creating the environment')
                if not plan.python.is_file(): raise ValueError(f'installed venv interpreter missing: {plan.python}')
                hashes = _validate_payload(root, plan.prefix)
                _registration_payload(plan.prefix, root, plan.version)
                receipt = {'owner': OWNER, 'schema_version': 1, 'prefix': str(plan.prefix), 'python': str(plan.python),
                           'version': plan.version, 'fingerprint': plan.fingerprint, 'dependencies': list(plan.dependencies),
                           'package_root': str(root), 'payload_hashes': hashes}
                if plan.source_revision: receipt['source_revision'] = dict(plan.source_revision)
                write_json_atomic(_receipt(plan.prefix), receipt)
                (plan.prefix / '.installing.json').unlink()
            except BaseException:
                # This process created the final prefix and has not activated
                # it. Only its own failed staging directory may be removed.
                if plan.prefix.exists(): shutil.rmtree(plan.prefix)
                raise
        active_path = plan.assets_dest / 'runtime.json'
        active = _object(active_path) if active_path.exists() else {}
        committed_path = plan.prefix / 'runtime.json'
        committed = _object(committed_path) if committed_path.exists() else {}
        prior_by_key = {(r['agent'], r.get('target', '')): r for r in committed.get('registrations', [])}
        selected_keys = {(r['agent'], r.get('target', '')) for r in plan.registrations}
        registrations = [r for r in active.get('registrations', []) if (r['agent'], r.get('target', '')) not in selected_keys]
        applied = []
        try:
            for registration in plan.registrations:
                registration['runtime_prefix'] = str(plan.prefix)
                registration['fingerprint'] = plan.fingerprint
                key = (registration['agent'], registration.get('target', ''))
                prior = prior_by_key.get(key)
                if prior and registration['kind'] == 'copy':
                    current = _registration_receipt(Path(registration['target']))
                    if current['fingerprint'] == plan.fingerprint:
                        registrations.append(prior); continue
                if registration['kind'] == 'copy': _register_copy(registration, root, plan.fingerprint)
                else:
                    _backup_adapter(registration)
                    applied.append(registration)
                    if registration['agent'] == 'gemini' and any(p.get('backup') for p in registration.get('paths', [])):
                        register(registration['undo'])
                    # Include an adapter being applied so a partly successful
                    # native command can restore all explicitly backed paths.
                    register(registration['argv'])
                    for item in registration.get('paths', []):
                        target = Path(item['target'])
                        if target.exists() or target.is_symlink(): item['installed_sha256'] = _tree_hash(target)
                if registration not in applied: applied.append(registration)
                registrations.append(registration)
            manifest = {'schema_version': 1, 'owner': OWNER, 'python': str(plan.python), 'prefix': str(plan.prefix),
                        'version': plan.version, 'fingerprint': plan.fingerprint, 'resources': str(root / 'data'),
                        'skills': str(root / 'skills'), 'owned_paths': [str(plan.prefix), str(plan.assets_dest / 'runtime.json'), *(r['target'] for r in registrations if r['kind'] == 'copy')],
                        'registrations': registrations}
            if plan.source_revision: manifest['source_revision'] = dict(plan.source_revision)
            write_json_atomic(committed_path, manifest)
            try:
                write_json_atomic(active_path, manifest)
            except BaseException:
                if committed: write_json_atomic(committed_path, committed)
                else: committed_path.unlink(missing_ok=True)
                raise
        except BaseException:
            for registration in reversed(applied):
                if registration['kind'] == 'copy': _restore_copy(registration, plan.fingerprint)
                else: _restore_adapter(registration)
            raise
        return manifest


@dataclass(frozen=True)
class UninstallConfig:
    assets_dest: Path | None = None
    home: Path | None = None
    agents: tuple[str, ...] = ()
    dry_run: bool = False
    run_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class UninstallationPlan:
    config: UninstallConfig
    assets_dest: Path
    fingerprint: str
    paths: tuple[Path, ...]
    commands: tuple[tuple[str, ...], ...]
    retained_runtimes: tuple[Path, ...]
    manifest_sha256: str
    restorations: tuple[dict, ...]

    def to_record(self):
        return {'assets_dest': str(self.assets_dest), 'remove': [str(p) for p in self.paths],
                'commands': [list(c) for c in self.commands], 'owned_paths': [str(p) for p in self.paths],
                'restore': list(self.restorations),
                'retained_runtimes': [str(p) for p in self.retained_runtimes],
                'retention_reason': 'Immutable runtimes may be executing or pinned by existing runs; retained deliberately.'}


def plan_uninstall(config: UninstallConfig) -> UninstallationPlan:
    home = _absolute(config.home or Path.home())
    destination = _safe_assets(config.assets_dest or default_assets_dest(), home)
    manifest_path = destination / 'runtime.json'
    if manifest_path.is_symlink() or not manifest_path.is_file(): raise ValueError(f'no owned active runtime manifest: {manifest_path}')
    manifest = _object(manifest_path)
    if manifest.get('owner') != OWNER or manifest.get('schema_version') != 1: raise ValueError('unowned runtime manifest')
    prefix = _absolute(manifest['prefix'])
    if not prefix.is_relative_to(destination / 'runtimes') or prefix.is_symlink() or prefix.parent.is_symlink(): raise ValueError('runtime prefix escapes owned runtimes directory')
    receipt = _owned_receipt(prefix, manifest['fingerprint']); _verify_payload(receipt, prefix)
    paths, commands, restorations = [], [], []
    if not set(config.agents).issubset({*AGENTS, 'assets', 'skills'}): raise ValueError('unknown uninstall target')
    for registration in manifest.get('registrations', []):
        selected = not config.agents or registration['agent'] in config.agents or ('skills' in config.agents and registration['kind'] == 'copy')
        if not selected: continue
        registration_prefix = _absolute(registration.get('runtime_prefix', str(prefix)))
        if not registration_prefix.is_relative_to(destination / 'runtimes') or registration_prefix.is_symlink():
            raise ValueError('registration runtime prefix escapes owned runtimes')
        _owned_receipt(registration_prefix, registration.get('fingerprint', manifest['fingerprint']))
        if registration['kind'] == 'copy':
            target = _absolute(registration['target'])
            roots = [home / '.codex/skills', home / '.claude/skills']
            if not any(target.parent == root for root in roots) or target.name not in SKILL_NAMES:
                raise ValueError(f'unowned registration target: {target}')
            for ancestor in (target, *target.parents):
                if ancestor.is_symlink(): raise ValueError(f'symlink escape in registration: {ancestor}')
            if target.exists():
                _registration_receipt(target)
                if registration.get('backup'):
                    backup = Path(registration['backup'])
                    if not backup.is_relative_to(registration_prefix / 'registration-backups') or _tree_hash(backup) != registration.get('existing_sha256'):
                        raise ValueError(f'unowned or changed registration backup: {backup}')
                paths.append(target)
                if registration.get('backup'):
                    restorations.append({'target': str(target), 'backup': registration['backup']})
        else:
            # Reconstruct the known adapter rather than execute arbitrary JSON.
            agent = registration['agent']
            allowed = {str(p) for p in _external_paths(agent, home)}
            for item in registration.get('paths', []):
                if item['target'] not in allowed: raise ValueError('adapter target is not a standard owned path')
                target = Path(item['target'])
                for ancestor in target.parents:
                    if ancestor.is_symlink(): raise ValueError(f'symlink escape in adapter registration: {ancestor}')
                if item.get('installed_sha256') and (not (target.exists() or target.is_symlink()) or _tree_hash(target) != item['installed_sha256']):
                    raise ValueError(f'edited adapter registration: {target}')
                if item.get('backup'):
                    backup = Path(item['backup'])
                    if not backup.is_relative_to(registration_prefix / 'registration-backups') or _tree_hash(backup) != item.get('existing_sha256'):
                        raise ValueError(f'unowned or changed adapter backup: {backup}')
                    restorations.append({'target': str(target), 'backup': str(backup)})
            if agent == 'gemini': commands.append(('gemini', 'extensions', 'uninstall', 'ai-skill-collections'))
            elif agent in PROFILES: commands.append(('npx', '-y', 'skills', 'remove', *(s for s in SKILL_NAMES), '-a', PROFILES[agent], '--yes', '--global'))
            else: raise ValueError('unowned registration adapter')
    if not config.agents or 'assets' in config.agents: paths.append(manifest_path)
    retained = tuple(sorted(p for p in (destination / 'runtimes').iterdir() if p.is_dir()))
    return UninstallationPlan(config, destination, manifest['fingerprint'], tuple(paths), tuple(commands), retained, _hash(manifest_path), tuple(restorations))


def apply_uninstall(plan: UninstallationPlan, *, registrar=None):
    if plan.config.dry_run: return plan.to_record()
    register = registrar or (lambda argv: _run_registration(argv, home=plan.config.home or Path.home()))
    # Re-plan under the mutation lock so changed ownership never authorizes a
    # deletion based on an earlier snapshot.
    with DirectoryLock(plan.assets_dest / '.installation', timeout_seconds=.1):
        current = plan_uninstall(plan.config)
        if current.manifest_sha256 != plan.manifest_sha256 or current.fingerprint != plan.fingerprint or current.paths != plan.paths or current.commands != plan.commands:
            raise ValueError('installation ownership changed after uninstall planning')
        for command in plan.commands: register(command)
        manifest = _object(plan.assets_dest / 'runtime.json')
        for registration in manifest.get('registrations', []):
            if registration['kind'] == 'command' and (not plan.config.agents or registration['agent'] in plan.config.agents):
                _restore_adapter(registration)
        by_target = {r.get('target'): r for r in manifest.get('registrations', []) if r['kind'] == 'copy'}
        for path in plan.paths:
            if path.is_dir():
                registration = by_target[str(path)]
                _restore_copy(registration, registration.get('fingerprint', plan.fingerprint))
            else: path.unlink()
        active_path = plan.assets_dest / 'runtime.json'
        if active_path.exists() and plan.config.agents:
            manifest['registrations'] = [r for r in manifest.get('registrations', []) if not (
                r['agent'] in plan.config.agents or ('skills' in plan.config.agents and r['kind'] == 'copy'))]
            removed = {str(path) for path in plan.paths}
            manifest['owned_paths'] = [p for p in manifest.get('owned_paths', []) if p not in removed]
            write_json_atomic(active_path, manifest)
    return plan.to_record()


def _selected_runtime(record, *, pinned):
    if type(record.get('schema_version')) is not int or record['schema_version'] != 1: raise ValueError('unsupported runtime manifest schema')
    if pinned and (record.get('engine') != 'python' or type(record.get('engine_version')) is not int or record.get('engine_version') != 1): raise ValueError('incompatible pinned engine/version')
    python = record.get('python')
    if not isinstance(python, str) or not Path(python).is_absolute() or not Path(python).is_file():
        raise ValueError(('pinned' if pinned else 'active') + ' runtime interpreter is missing; refusing fallback')
    return _absolute(python)


def pair_runtime_pin(run_dir: Path) -> dict | None:
    """Recognize an explicit pair pin without reserving project config.json."""
    config = load_json(_absolute(run_dir) / 'config.json', missing=None)
    if not isinstance(config, dict) or 'engine_runtime' not in config:
        return None
    pin = config['engine_runtime']
    if not isinstance(pin, dict):
        raise ValueError('invalid pinned pair runtime')
    return pin


def resolve_runtime(run_dir: Path | None = None, *, assets_dest: Path | None = None) -> Path:
    """Select a pinned runtime before the active default; never resolve venv links."""
    if run_dir is not None:
        run = _absolute(run_dir)
        engine = run / '.run-with-it/engine.json'
        if engine.exists(): return _selected_runtime(_object(engine), pinned=True)
        pin = pair_runtime_pin(run)
        if pin is not None:
            return _selected_runtime(pin, pinned=True)
    manifest = _absolute(assets_dest or default_assets_dest()) / 'runtime.json'
    if manifest.exists(): return _selected_runtime(_object(manifest), pinned=False)
    return _absolute(sys.executable)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    uninstall = bool(args and args[0] == 'uninstall')
    if uninstall: args.pop(0)
    parser = argparse.ArgumentParser(prog='ai-skills ' + ('uninstall' if uninstall else 'install'), description=__doc__)
    parser.add_argument('--dry-run', action='store_true'); parser.add_argument('--list', action='store_true')
    parser.add_argument('--no-color', action='store_true'); parser.add_argument('--only', action='append', choices=(*AGENTS, 'assets', 'skills') if uninstall else AGENTS, default=[])
    parser.add_argument('--assets-dest', type=Path); parser.add_argument('--home', type=Path)
    if not uninstall:
        parser.add_argument('--source', type=Path)
        parser.add_argument('--replace-existing', action='store_true')
    try:
        options = parser.parse_args(args)
        if options.list:
            print('\n'.join(AGENTS)); return 0
        common = {'assets_dest': options.assets_dest, 'home': options.home, 'agents': tuple(options.only), 'dry_run': options.dry_run}
        if uninstall:
            plan = plan_uninstall(UninstallConfig(**common))
            result = plan.to_record() if options.dry_run else apply_uninstall(plan)
        else:
            plan = plan_install(InstallConfig(source=options.source, replace_existing=options.replace_existing, **common))
            result = plan.to_record() if options.dry_run else apply_install(plan)
        print(json.dumps(result, indent=2)); return 0
    except SystemExit as error: return int(error.code)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'ai-skills installation: {error}', file=sys.stderr); return 2
