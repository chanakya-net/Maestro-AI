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
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from ai_skills.installation import AGENTS, InstallConfig, apply_install, plan_install

OFFICIAL_REPOSITORY = 'https://github.com/chanakya-net/Maestro-AI.git'
OFFICIAL_BRANCH = 'main'
CLONE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class UpdateConfig:
    assets_dest: Path | None = None
    home: Path | None = None
    agents: tuple[str, ...] = ()


def _git(*args, cwd=None, timeout=CLONE_TIMEOUT_SECONDS):
    git = shutil.which('git')
    if not git: raise ValueError('official source requires git on PATH')
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('GIT_') and not key.upper().endswith('_PROXY')
           and key.upper() not in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run([git, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'official source git timed out after {timeout}s') from error
    if result.returncode:
        raise ValueError(f'official source git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout.strip()


@contextmanager
def official_source(repository=None, branch=None, *, timeout=CLONE_TIMEOUT_SECONDS):
    """Yield ``(checkout, sha)`` for the branch tip; the checkout is removed on exit."""
    repository = repository or OFFICIAL_REPOSITORY
    branch = branch or OFFICIAL_BRANCH
    with tempfile.TemporaryDirectory(prefix='ai-skills-update-') as scratch:
        checkout = Path(scratch) / 'source'
        template = Path(scratch) / 'empty-template'
        template.mkdir()
        _git('-c', 'core.autocrlf=false', '-c', f'core.hooksPath={template}', '-c', f'core.attributesFile={os.devnull}', 'clone', '--template', str(template), '--quiet', '--depth', '1', '--single-branch', '--no-tags',
             '--branch', branch, '--', repository, str(checkout), timeout=timeout)
        sha = _git('rev-parse', f'refs/remotes/origin/{branch}^{{commit}}', cwd=checkout, timeout=timeout)
        yield checkout, sha


def run_update(config: UpdateConfig, *, repository=None, package_installer=None, registrar=None):
    repository = repository or OFFICIAL_REPOSITORY
    with official_source(repository) as (checkout, sha):
        revision = {'repository': repository, 'branch': OFFICIAL_BRANCH, 'commit': sha}
        plan = plan_install(InstallConfig(source=checkout, assets_dest=config.assets_dest, home=config.home,
                                          agents=config.agents, source_revision=revision))
        return apply_install(plan, package_installer=package_installer, registrar=registrar)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ai-skills update', description=__doc__)
    parser.add_argument('--assets-dest', type=Path); parser.add_argument('--home', type=Path)
    parser.add_argument('--only', action='append', choices=AGENTS, default=[])
    try:
        options = parser.parse_args(argv)
        with redirect_stdout(sys.stderr):
            manifest = run_update(UpdateConfig(options.assets_dest, options.home, tuple(options.only)))
        print(json.dumps(manifest, indent=2)); return 0
    except SystemExit as error: return int(error.code or 0)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'ai-skills update: {error}', file=sys.stderr)
        # Rollback recovery notes name preserved output and backups needing inspection.
        for note in getattr(error, '__notes__', ()): print(f'ai-skills update: {note}', file=sys.stderr)
        return 2
