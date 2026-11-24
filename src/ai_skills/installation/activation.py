"""Transactional runtime staging, activation, recovery, and install application."""
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
    _AdapterActivation, _adapter_state, _clean_interrupted,
    _copy_tree, _install_package, _isolated_environment,
    _pip_environment, _register_copy, _registration_payload,
    _remove_tree, _restore_adapter, _restore_backup,
    _restore_copy, _run_child, _run_registration, _runtime_root,
    _validate_payload, _verify_interpreter, _verify_payload,
)





def _write_bytes_atomic(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        replace_file_atomic(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def _transaction_digest(path, kind):
    if kind == 'tree': return _adapter_state(path)
    if path.is_symlink(): raise ValueError(f'symlinked transaction metadata: {path}')
    return _hash(path) if path.exists() else None


class _ActivationTransaction:
    """Installation-local write-ahead journal. Recovery never executes saved argv.

    Before-images outlive temporary sources and remain with the receipted runtime.
    Only observed successful adapter output is owned; an unfinished external call
    cannot be inferred safe merely because its installer process has died.
    Stop the adapter, verify affected targets against their backups and repair
    them manually, then remove the journal before retrying an unfinished call.
    """

    def __init__(self, destination, state):
        self.destination, self.state = destination, state
        self.path = _transaction_path(destination)
        self.committed = False

    @classmethod
    def prepare(cls, plan):
        state = {'schema_version': 1, 'owner': OWNER, 'host': socket.gethostname(),
                 'pid': os.getpid(), 'create_time': psutil.Process().create_time(), 'token': uuid.uuid4().hex,
                 'home': str(_absolute(plan.config.home or Path.home())), 'destination': str(plan.assets_dest),
                 'prefix': str(plan.prefix), 'fingerprint': plan.fingerprint,
                 'receipt_sha256': _hash(_receipt(plan.prefix)), 'phase': 'prepared',
                 'entries': [], 'external_pending': [], 'released': False}
        transaction = cls(plan.assets_dest, state)
        for path in (plan.prefix / 'runtime.json', plan.assets_dest / 'runtime.json'):
            transaction.snapshot(path, 'file')
        for registration in plan.registrations:
            items = [registration] if registration['kind'] == 'copy' else registration.get('paths', [])
            for item in items:
                target = Path(item['target'])
                if _adapter_state(target) != item.get('existing_sha256'):
                    raise ValueError(f'registration changed after planning: {target}')
                transaction.snapshot(target, 'tree')
        try: transaction.save()
        except BaseException as error:
            if transaction.path.exists(): transaction.failed(error)
            else: error._installation_recovery = 'restored'
            raise
        return transaction

    def backup_root(self):
        return Path(self.state['prefix']) / 'registration-backups' / ('activation-' + self.state['token'])

    def snapshot(self, path, kind):
        if any(item['target'] == str(path) for item in self.state['entries']): return
        digest = _transaction_digest(path, kind)
        backup = self.backup_root() / str(len(self.state['entries']))
        _safe_assets(backup.parent, Path(self.state['home']))
        if digest is not None:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if kind == 'tree': _copy_tree(path, backup)
            else: _write_bytes_atomic(backup, path.read_bytes())
            if _transaction_digest(backup, kind) != digest:
                raise ValueError(f'transaction before-image changed: {backup}')
        self.state['entries'].append({'target': str(path), 'kind': kind, 'before': digest,
                                      'after': digest, 'backup': str(backup)})

    def save(self):
        write_json_atomic(self.path, self.state)

    def entry(self, target):
        return next(item for item in self.state['entries'] if item['target'] == str(target))

    def expect(self, target, digest):
        self.entry(target)['after'] = digest
        self.state['phase'] = 'activating'
        self.save()

    def command(self, registration, argv, register):
        self.state['external_pending'] = [item['target'] for item in registration.get('paths', [])]
        self.save()  # Intent precedes Gemini undo as well as installation.
        try:
            register(argv)
        except Exception as error:
            # The call returned, but failed output remains unverified. Keep the
            # last successful digest; restore only if the target still matches it.
            self.state['external_pending'] = []
            try: self.save()
            except (OSError, ValueError) as failure:
                error.add_note(f'could not record adapter exit in {self.path}: {failure}')
            raise

    def settle(self, registration):
        for item in registration.get('paths', []):
            self.entry(item['target'])['after'] = _adapter_state(item['target'])
        self.state['external_pending'] = []
        self.save()

    @classmethod
    def load(cls, destination, home):
        path = _transaction_path(destination)
        if path.is_symlink(): raise ValueError(f'symlinked recovery journal: {path}')
        transaction = cls(destination, _object(path))
        transaction.validate(home)
        state = transaction.state
        released = state.get('released') is True and state['phase'] in ('incomplete', 'committed') and not state['external_pending']
        if not released and not DirectoryLock._proven_dead(state):
            raise ValueError(f'recovery journal has a live or unknown owner: {path}')
        return transaction

    def validate(self, home):
        state = self.state
        if (type(state.get('schema_version')) is not int or state['schema_version'] != 1
                or state.get('owner') != OWNER or state.get('home') != str(home)
                or state.get('destination') != str(self.destination)
                or not isinstance(state.get('token'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', str(state.get('token', '')))
                or state.get('phase') not in ('prepared', 'activating', 'restoring', 'incomplete', 'committed')
                or state.get('host') != socket.gethostname() or type(state.get('pid')) is not int
                or state['pid'] <= 0 or type(state.get('create_time')) not in (int, float)
                or not 0 < state['create_time'] < float('inf')
                or not re.fullmatch(r'[0-9a-f]{64}', str(state.get('fingerprint', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(state.get('receipt_sha256', '')))):
            raise ValueError(f'invalid or unknown-owner recovery journal: {self.path}')
        prefix = _absolute(state.get('prefix', ''))
        if str(prefix) != state.get('prefix') or prefix.parent != self.destination / 'runtimes':
            raise ValueError(f'escaped recovery runtime: {prefix}')
        _safe_assets(prefix, home)
        _owned_receipt(prefix, state.get('fingerprint'))
        if _hash(_receipt(prefix)) != state.get('receipt_sha256'):
            raise ValueError(f'changed recovery runtime receipt: {prefix}')
        self.validate_entries(home, prefix)

    def validate_entries(self, home, prefix):
        allowed = {str(home / root / name) for root in ('.codex/skills', '.claude/skills') for name in SKILL_NAMES}
        allowed.update(str(path) for agent in AGENTS if agent not in ('codex', 'claude') for path in _external_paths(agent, home))
        files = {str(prefix / 'runtime.json'), str(self.destination / 'runtime.json')}
        entries, pending = self.state.get('entries'), self.state.get('external_pending')
        if not isinstance(entries, list) or not isinstance(pending, list):
            raise ValueError(f'invalid recovery entries: {self.path}')
        seen = set()
        for index, item in enumerate(entries):
            if not isinstance(item, dict) or not {'target', 'kind', 'backup', 'before', 'after'} <= item.keys():
                raise ValueError('invalid recovery entry')
            target, kind = item.get('target'), item.get('kind')
            if not isinstance(target, str) or kind not in ('tree', 'file') or target not in (allowed if kind == 'tree' else files) or target in seen:
                raise ValueError(f'unowned recovery target: {target}')
            seen.add(target)
            if item.get('backup') != str(self.backup_root() / str(index)):
                raise ValueError(f'escaped recovery backup: {item.get("backup")}')
            for key in ('before', 'after'):
                if item.get(key) is not None and not re.fullmatch(r'[0-9a-f]{64}', str(item[key])):
                    raise ValueError(f'invalid recovery digest: {target}')
        if not files <= seen or any(not isinstance(target, str) or target not in allowed or target not in seen for target in pending):
            raise ValueError('invalid unfinished adapter targets')

    def auxiliary(self, item, suffix):
        target = Path(item['target'])
        return target.with_name('.' + target.name + '.' + suffix + '.' + self.state['token'])

    def checked_backup(self, item):
        backup = Path(item['backup'])
        for ancestor in backup.parents:
            if ancestor.is_symlink(): raise ValueError(f'symlinked recovery backup: {backup}')
        if item['before'] is not None and _transaction_digest(backup, item['kind']) != item['before']:
            raise ValueError(f'recovery backup missing or changed: {backup}')
        return backup

    def cleanup_entry(self, item):
        if item['kind'] != 'tree': return
        for suffix, digest in (('staging', item['after']), ('previous', item['before']),
                               ('restoring', item['before']), ('displaced', item['after'])):
            path = self.auxiliary(item, suffix)
            if not path.exists() and not path.is_symlink(): continue
            scratch = suffix == 'staging' and item['after'] == item['before']
            if not scratch and _adapter_state(path) != digest:
                raise ValueError(f'edited recovery staging content preserved: {path}')
            _remove_tree(path)

    def restore_entry(self, item):
        target = Path(item['target'])
        backup = self.checked_backup(item)
        actual = _transaction_digest(target, item['kind'])
        if item['target'] in self.state['external_pending']:
            raise ValueError(f'unfinished adapter execution; output preserved: {target}; backup: {backup}; '
                             f'stop the adapter, verify {target} against {backup}, '
                             f'repair it manually, then remove {self.path} before retrying')
        if actual == item['before']:
            self.cleanup_entry(item); return
        if actual is not None and actual != item['after']:
            raise ValueError(f'uncommitted or edited content preserved: {target}; backup: {backup}')
        if item['kind'] == 'file':
            if item['before'] is None: target.unlink(missing_ok=True)
            else: _write_bytes_atomic(target, backup.read_bytes())
        else:
            self.restore_tree(item, target, backup, actual)
        if _transaction_digest(target, item['kind']) != item['before']:
            raise ValueError(f'restoration verification failed: {target}; backup: {backup}')
        self.cleanup_entry(item)

    def restore_tree(self, item, target, backup, actual):
        staged, displaced = (self.auxiliary(item, suffix) for suffix in ('restoring', 'displaced'))
        if item['before'] is not None:
            if (staged.exists() or staged.is_symlink()) and _adapter_state(staged) != item['before']:
                _remove_tree(staged)  # An interrupted copy is scratch, unlike displaced content.
            if not staged.exists() and not staged.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True); _copy_tree(backup, staged)
            if _adapter_state(staged) != item['before']: raise ValueError(f'changed recovery staging: {staged}')
        if displaced.exists() or displaced.is_symlink():
            if _adapter_state(displaced) != item['after']: raise ValueError(f'edited displaced target: {displaced}')
        if actual is not None:
            if displaced.exists() or displaced.is_symlink(): raise ValueError(f'conflicting recovery target: {target}')
            target.rename(displaced)
        if item['before'] is not None: staged.rename(target)

    def restore(self):
        failures = []
        self.state['phase'] = 'restoring'
        try: self.save()
        except (OSError, ValueError) as error: failures.append(str(error))
        for item in reversed(self.state['entries']):
            try: self.restore_entry(item)
            except (OSError, ValueError) as error: failures.append(str(error))
        if not failures:
            try: self.path.unlink()
            except OSError as error: failures.append(str(error))
        if failures:
            self.state.update(phase='incomplete', released=not bool(self.state['external_pending']))
            try: self.save()
            except (OSError, ValueError) as error: failures.append(str(error))
        return failures

    def verify_committed(self):
        if self.state['external_pending']: raise ValueError('committed journal has an unfinished adapter')
        for item in self.state['entries']:
            if _transaction_digest(Path(item['target']), item['kind']) != item['after']:
                raise ValueError(f'committed installation changed: {item["target"]}; journal: {self.path}')

    def finish(self):
        self.verify_committed()
        self.state.update(phase='committed', released=True)
        self.save()
        self.committed = True
        self.cleanup()

    def cleanup(self):
        for item in self.state['entries']: self.cleanup_entry(item)
        self.path.unlink()

    def failed(self, error):
        # A writer may raise after replacement: inspect the durable phase before
        # deciding to undo. Cleanup failure after commit preserves the new runtime.
        if self.committed:
            error._installation_recovery = 'committed; cleanup incomplete'
            return
        try: durable = _object(self.path)
        except (OSError, ValueError) as failure:
            error._installation_recovery = 'incomplete'
            error.add_note(f'rollback incomplete: unreadable journal {self.path}: {failure}')
            return
        if durable.get('phase') == 'committed':
            error._installation_recovery = 'committed; cleanup incomplete'
            return
        failures = self.restore()
        error._installation_recovery = 'incomplete' if failures else 'restored'
        for failure in failures:
            error.add_note(f'rollback incomplete: {failure}; journal: {self.path}')


def _recover_installation_locked(destination, home):
    path = _transaction_path(destination)
    if not path.exists() and not path.is_symlink(): return None
    transaction = None
    try:
        transaction = _ActivationTransaction.load(destination, home)
        if transaction.state['phase'] == 'committed':
            transaction.verify_committed(); transaction.cleanup(); return 'committed'
        failures = transaction.restore()
        if failures: raise ValueError('; '.join(failures))
        return 'restored'
    except (OSError, ValueError) as error:
        committed = transaction is not None and transaction.state['phase'] == 'committed'
        error._installation_recovery = 'committed; cleanup incomplete' if committed else 'incomplete'
        error.add_note(f'{"committed cleanup" if committed else "rollback"} incomplete; recovery journal retained: {path}')
        raise


def _recover_installation(assets_dest=None, home=None, *, dry_run=False):
    home = _absolute(home or Path.home())
    destination = _safe_assets(assets_dest or default_assets_dest(), home)
    if dry_run:
        _require_no_transaction(destination); return None
    path = _transaction_path(destination)
    if not path.exists() and not path.is_symlink(): return None
    with DirectoryLock(destination / '.installation', timeout_seconds=.1):
        return _recover_installation_locked(destination, home)


def _stage_runtime(plan, package_installer):
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
            _verify_interpreter(plan.python, root)
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
            if plan.prefix.exists() and not _receipt(plan.prefix).exists(): shutil.rmtree(plan.prefix)
            raise
    return root


def _activate_runtime(plan, root, register):
    active_path = plan.assets_dest / 'runtime.json'
    active = _object(active_path) if active_path.exists() else {}
    committed_path = plan.prefix / 'runtime.json'
    committed = _object(committed_path) if committed_path.exists() else {}
    prior_by_key = {(r['agent'], r.get('target', '')): r for r in committed.get('registrations', [])}
    selected_keys = {(r['agent'], r.get('target', '')) for r in plan.registrations}
    retained = [r for r in active.get('registrations', []) if (r['agent'], r.get('target', '')) not in selected_keys]
    registrations = list(retained)
    transaction = _ActivationTransaction.prepare(plan)
    adapters = _AdapterActivation(transaction)
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
            if not plan.config.refresh_registrations and prior and registration['kind'] == 'copy' and (target.exists() or target.is_symlink()):
                try: current = _registration_receipt(target)
                except ValueError: current = None
                if current and current['fingerprint'] == plan.fingerprint:
                    registrations.append(prior); continue
            if registration['kind'] == 'copy': _register_copy(registration, root, plan.fingerprint, transaction)
            else:
                adapters.prepare(registration)
                if registration['agent'] == 'gemini' and any(p.get('backup') for p in registration.get('paths', [])):
                    transaction.command(registration, registration['undo'], register); adapters.settle(registration)
                # Include an adapter being applied so a partly successful
                # native command can restore all explicitly backed paths.
                transaction.command(registration, registration['argv'], register)
                adapters.record(registration)
            registrations.append(registration)
        adapters.share(retained)
        manifest = {'schema_version': 1, 'owner': OWNER, 'python': str(plan.python), 'prefix': str(plan.prefix),
                    'version': plan.version, 'fingerprint': plan.fingerprint, 'resources': str(root / 'data'),
                    'skills': str(root / 'skills'), 'owned_paths': [str(plan.prefix), str(plan.assets_dest / 'runtime.json'), *(r['target'] for r in registrations if r['kind'] == 'copy')],
                    'registrations': registrations}
        if plan.source_revision: manifest['source_revision'] = dict(plan.source_revision)
        for path in (committed_path, active_path):
            payload = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n').replace('\n', os.linesep).encode()
            transaction.expect(path, hashlib.sha256(payload).hexdigest())
            write_json_atomic(path, manifest)
        transaction.finish()
    except BaseException as error:
        transaction.failed(error)
        raise
    return manifest


def _check_install_source(plan):
    _, _, dependencies, payload = _source(plan.source)
    if payload != plan.payload_fingerprint or _dependency_pins(dependencies) != plan.dependencies:
        raise ValueError('installation source or dependency versions changed after planning')


def apply_install(plan: InstallationPlan, *, package_installer=None, registrar=None):
    home = _absolute(plan.config.home or Path.home())
    _recover_installation(plan.assets_dest, home, dry_run=plan.config.dry_run)
    if plan.config.dry_run: return plan.to_record()
    register = registrar or (lambda argv: _run_registration(argv, home=home))
    _check_install_source(plan)
    _safe_assets(plan.assets_dest, home)
    result = None
    try:
        with DirectoryLock(plan.assets_dest / '.installation', timeout_seconds=.1):
            _recover_installation_locked(plan.assets_dest, home)
            active_path = plan.assets_dest / 'runtime.json'
            snapshot = active_path.read_bytes() if active_path.exists() else None
            if snapshot != plan.active_snapshot or active_path.is_symlink():
                plan = plan_install(plan.config)
            if active_path.is_symlink():
                raise ValueError(f'active runtime manifest is a symlink; refusing to replace it: {active_path}')
            _check_install_source(plan)
            if plan.config.skip_current_revision and plan.source_revision and snapshot is not None:
                active = _object(active_path)
                revision = active.get('source_revision')
                if isinstance(revision, dict) and revision.get('commit') == plan.source_revision['commit']:
                    return active
            root = _stage_runtime(plan, package_installer)
            _check_install_source(plan)
            _revalidate_activation(plan)
            result = _activate_runtime(plan, root, register)
        return result
    except BaseException as error:
        if result is not None: error._installation_recovery = 'committed; lock release failed'
        raise


def _revalidate_activation(plan):
    active = plan.assets_dest / 'runtime.json'
    if active.is_symlink() or (active.read_bytes() if active.exists() else None) != plan.active_snapshot:
        raise ValueError('active runtime changed during staging')
    for registration in plan.registrations:
        items = [registration] if registration['kind'] == 'copy' else registration.get('paths', [])
        for item in items:
            if _adapter_state(item['target']) != item.get('existing_sha256'):
                raise ValueError(f'registration changed after planning: {item["target"]}')
            baseline = _uninstall_view(item)
            if not baseline.get('backup') or 'original_backup' not in item: continue
            backup = Path(baseline['backup'])
            if not _owned_backup(backup, plan.assets_dest) or _tree_hash(backup) != baseline.get('existing_sha256'):
                raise ValueError(f'unowned or changed original registration backup: {backup}')

