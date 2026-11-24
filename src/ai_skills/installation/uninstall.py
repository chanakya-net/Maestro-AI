"""Uninstallation planning/application, runtime resolution, and CLI entrypoint."""
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
from ai_skills.installation.runtime import (
    _remove_tree, _restore_adapter, _restore_backup, _restore_copy,
    _run_registration, _verify_payload,
)
from ai_skills.installation.activation import (
    _recover_installation, _recover_installation_locked,
    apply_install,
)

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
    _require_no_transaction(destination)
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
        _require_no_transaction(plan.assets_dest)
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
        _recover_installation(options.assets_dest, options.home, dry_run=options.dry_run)
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
