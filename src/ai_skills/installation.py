"""Plan and install immutable native Python runtimes and owned registrations.

Plans perform read-only discovery. Only apply functions create environments,
run pip/provider registration, activate a manifest, or remove owned files.
Global Python/Node installations and provider permission settings are untouched.
"""
from __future__ import annotations

import argparse
import ast
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
import tempfile
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


def _transition_backup(prefix, agent, name, digest):
    """Rollback snapshot of one prior registration state for this candidate.

    Keyed by the prior content digest so reactivating a retained prefix after a
    different registration was active gets its own snapshot instead of colliding
    with the one taken on that prefix's first activation.
    """
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
    if pins: pip_commands.append((str(python), '-I', '-m', 'pip', 'install', *pins))
    pip_commands.append((str(python), '-I', '-m', 'pip', 'install', '--no-deps', str(source)))
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
    external_owned, prior_entries = {}, {}
    snapshot = active.read_bytes() if active.exists() else None
    if snapshot is not None:
        previous = json.loads(snapshot)
        if not isinstance(previous, dict): raise ValueError(f'expected JSON object: {active}')
        if previous.get('owner') != OWNER: raise ValueError('active runtime manifest is unowned')
        for r in previous.get('registrations', []):
            if r.get('kind') == 'copy': prior_entries[r['target']] = r
            for p in r.get('paths', []):
                prior_entries[p['target']] = p
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
                    item.update(existing_sha256=digest, backup=_transition_backup(prefix, registration['agent'], hashlib.sha256(str(target).encode()).hexdigest(), digest))
                    if external_owned.get(str(target)) == digest: _carry_original(item, prior_entries.get(str(target)))
                else: _carry_original(item, prior_entries.get(str(target)))  # recreating a missing managed target
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
            digest = _tree_hash(target)
            registration.update(backup=_transition_backup(prefix, registration['agent'], registration['skill'], digest),
                                existing_sha256=digest, existing_owned=owned)
            if owned: _carry_original(registration, prior_entries.get(str(target)))
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
                            cwd=Path(python).parents[1], env=_isolated_environment())
    return Path(json.loads(result.stdout))


def _verify_interpreter(python, package_root):
    """A retained runtime must launch and import its own recorded package."""
    if not python.is_file(): raise ValueError(f'installed venv interpreter missing: {python}')
    try: root = _runtime_root(python, verify_dependencies=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'installed venv interpreter unusable: {python}: {error}') from error
    if root.resolve() != Path(package_root).resolve():
        raise ValueError(f'installed venv interpreter imports an unexpected package: {root}')


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
    _run_child([str(plan.python), '-I', '-m', 'pip', 'check'], cwd=cwd, env=env)
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

    def __init__(self):
        self.current, self.owners = {}, {}  # target -> digest written here | items sharing it

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

    def record(self, registration):
        for item in registration.get('paths', []):
            self.settle_owners(item['target'], self.owners[item['target']], _adapter_state(item['target']))

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

    def rollback(self, registration, handled):
        """Restore each target once; keep output this activation cannot explain."""
        failures = []
        for item in registration.get('paths', []):
            if item['target'] in handled: continue
            handled.add(item['target'])
            try: self._restore(item)
            except (OSError, ValueError) as failure: failures.append(str(failure))
        return failures

    def _restore(self, item):
        target = Path(item['target'])
        actual = _adapter_state(target)
        if actual != self.expected(item):
            kept = f'; its backup is kept at {item["backup"]}' if item.get('backup') else ''
            raise ValueError(f'unverified adapter output preserved at {target}{kept}')
        if actual is not None: _remove_tree(target)
        if item.get('backup'): _restore_backup(item)


def _rollback(applied, fingerprint, adapters):
    """Attempt to undo every applied registration; return what could not be restored."""
    failures, handled = [], set()
    for registration in reversed(applied):
        if registration['kind'] != 'copy':
            failures += adapters.rollback(registration, handled); continue
        try: _restore_copy(registration, fingerprint)
        except (OSError, ValueError) as failure: failures.append(str(failure))
    return failures


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


def apply_install(plan: InstallationPlan, *, package_installer=None, registrar=None):
    if plan.config.dry_run: return plan.to_record()
    register = registrar or (lambda argv: _run_registration(argv, home=plan.config.home or Path.home()))
    source, version, dependencies, payload = _source(plan.source)
    if payload != plan.payload_fingerprint or _dependency_pins(dependencies) != plan.dependencies:
        raise ValueError('installation source or dependency versions changed after planning')
    _safe_assets(plan.assets_dest, plan.config.home or Path.home())
    plan.assets_dest.mkdir(parents=True, exist_ok=True)
    with DirectoryLock(plan.assets_dest / '.installation', timeout_seconds=.1):
        active_path = plan.assets_dest / 'runtime.json'
        snapshot = active_path.read_bytes() if active_path.exists() else None
        if snapshot != plan.active_snapshot:
            plan = plan_install(plan.config)
        if plan.prefix.exists() and not _receipt(plan.prefix).exists(): _clean_interrupted(plan.prefix)
        if plan.prefix.exists():
            receipt = _owned_receipt(plan.prefix, plan.fingerprint); _verify_payload(receipt, plan.prefix)
            if plan.source_revision and receipt.get('source_revision') != plan.source_revision:
                raise ValueError('retained runtime source revision mismatch')
            root = Path(receipt['package_root'])
            _verify_interpreter(plan.python, root)
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
        retained = [r for r in active.get('registrations', []) if (r['agent'], r.get('target', '')) not in selected_keys]
        registrations = list(retained)
        applied, adapters = [], _AdapterActivation()
        try:
            # Every adapter target must still match planning before any adapter runs.
            for registration in plan.registrations:
                if registration['kind'] != 'copy': adapters.check(registration)
            for registration in plan.registrations:
                registration['runtime_prefix'] = str(plan.prefix)
                registration['fingerprint'] = plan.fingerprint
                key = (registration['agent'], registration.get('target', ''))
                prior = prior_by_key.get(key)
                target = Path(registration.get('target', ''))
                # Fast path only for a live registration; a partly uninstalled one is recreated.
                if prior and registration['kind'] == 'copy' and (target.exists() or target.is_symlink()):
                    current = _registration_receipt(target)
                    if current['fingerprint'] == plan.fingerprint:
                        registrations.append(prior); continue
                if registration['kind'] == 'copy': _register_copy(registration, root, plan.fingerprint)
                else:
                    adapters.prepare(registration)
                    applied.append(registration)
                    if registration['agent'] == 'gemini' and any(p.get('backup') for p in registration.get('paths', [])):
                        register(registration['undo']); adapters.settle(registration)
                    # Include an adapter being applied so a partly successful
                    # native command can restore all explicitly backed paths.
                    register(registration['argv'])
                    adapters.record(registration)
                if registration not in applied: applied.append(registration)
                registrations.append(registration)
            adapters.share(retained)
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
        except BaseException as error:
            # Keep the original failure; recovery state that needs inspection rides along.
            for failure in _rollback(applied, plan.fingerprint, adapters):
                error.add_note('rollback incomplete: ' + failure)
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
                baseline = _uninstall_view(registration)
                if baseline.get('backup'):
                    backup = Path(baseline['backup'])
                    if not _owned_backup(backup, destination) or _tree_hash(backup) != baseline.get('existing_sha256'):
                        raise ValueError(f'unowned or changed registration backup: {backup}')
                    restorations.append({'target': str(target), 'backup': baseline['backup']})
                paths.append(target)
        else:
            # Reconstruct the known adapter rather than execute arbitrary JSON.
            agent = registration['agent']
            allowed = {str(p) for p in _external_paths(agent, home)}
            for item in map(_uninstall_view, registration.get('paths', [])):
                if item['target'] not in allowed: raise ValueError('adapter target is not a standard owned path')
                target = Path(item['target'])
                for ancestor in target.parents:
                    if ancestor.is_symlink(): raise ValueError(f'symlink escape in adapter registration: {ancestor}')
                if item.get('installed_sha256') and (not (target.exists() or target.is_symlink()) or _tree_hash(target) != item['installed_sha256']):
                    raise ValueError(f'edited adapter registration: {target}')
                if item.get('backup'):
                    backup = Path(item['backup'])
                    if not _owned_backup(backup, destination) or _tree_hash(backup) != item.get('existing_sha256'):
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
                _restore_adapter(dict(registration, paths=[_uninstall_view(i) for i in registration.get('paths', [])]))
        by_target = {r.get('target'): r for r in manifest.get('registrations', []) if r['kind'] == 'copy'}
        for path in plan.paths:
            if path.is_dir():
                registration = by_target[str(path)]
                _restore_copy(_uninstall_view(registration), registration.get('fingerprint', plan.fingerprint))
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
