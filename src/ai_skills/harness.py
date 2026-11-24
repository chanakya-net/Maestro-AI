"""Per-run harness selection: which installed coding CLIs to use, and how much.

At the start of a new ``run-with-it`` run the orchestrator detects the
supported harnesses installed on this machine (Codex, Claude Code, Antigravity,
GitHub Copilot, ...), asks the user how work should be split between them, and
saves the answer to ``.run-with-it/harness-plan.json``. The router reads that
file from beside its usage ledger, so every routed worker honours it without
the coordinators having to pass it along.

A harness with a 0% share is excluded from the run entirely. The user's split
becomes the router's overall usage target; the per-band (and legacy per-role)
target tables keep their relative preferences but are reweighted toward the
user's split (see ``apply_plan``). Hard zero entries in those tables stay zero.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys

PLAN_FILE = 'harness-plan.json'
RUN_DIRECTORY = '.run-with-it'
SCHEMA_VERSION = 1
SOURCES = ('user', 'default', 'env')


class HarnessError(ValueError):
    """Invalid harness detection input, share split or saved plan."""


# --------------------------------------------------------------------------- shares


def round_to_100(weights: dict[str, float]) -> dict[str, int]:
    """Scale nonnegative weights to integers that sum to exactly 100.

    Uses largest-remainder rounding with a stable name tiebreak. All-zero input
    stays all zero.
    """
    total = float(sum(weights.values()))
    if total <= 0:
        return {name: 0 for name in weights}
    raw = {name: value * 100.0 / total for name, value in weights.items()}
    result = {name: int(math.floor(value)) for name, value in raw.items()}
    remainder = 100 - sum(result.values())
    order = sorted(raw, key=lambda name: (-(raw[name] - result[name]), name))
    for name in order[:remainder]:
        result[name] += 1
    return result


def _distribution(registry: dict) -> dict:
    return registry.get('model_routing', {}).get('usage_distribution', {})


def default_shares(registry: dict, agents) -> dict[str, int]:
    """Default split for the given installed harnesses, summing to 100."""
    agents = list(agents)
    if not agents:
        return {}
    distribution = _distribution(registry)
    preferred = distribution.get('harness_default_share_percent') or {}
    fallback = distribution.get('default_target_percent') or {}
    weights = {}
    for agent in agents:
        value = preferred.get(agent, fallback.get(agent))
        weights[agent] = float(value) if isinstance(value, (int, float)) and value > 0 else 0.0
    if not any(weights.values()):
        weights = {agent: 1.0 for agent in agents}
    return round_to_100(weights)


def canonical_agent(registry: dict, name: str) -> str:
    name = name.strip()
    return registry.get('aliases', {}).get(name, name)


def parse_shares(registry: dict, text: str) -> dict[str, int]:
    """Parse ``codex=50,claude=30,agy=20`` (``%`` and aliases accepted)."""
    shares: dict[str, int] = {}
    for token in (part.strip() for part in str(text).split(',')):
        if not token:
            continue
        if '=' not in token:
            raise HarnessError(f'share must look like harness=percent: {token!r}')
        name, value = (piece.strip() for piece in token.split('=', 1))
        agent = canonical_agent(registry, name)
        value = value.removesuffix('%').strip()
        if not value.isascii() or not value.isdigit():
            raise HarnessError(f'share for {name} must be a whole number from 0 to 100')
        if agent in shares:
            raise HarnessError(f'share for {agent} given more than once')
        shares[agent] = int(value)
    if not shares:
        raise HarnessError('no shares given')
    return shares


def validate_shares(shares: dict, installed) -> dict[str, int]:
    """Return a complete split over ``installed`` or raise ``HarnessError``.

    Installed harnesses the user did not mention get 0%. Every share must name
    an installed harness, be an integer 0-100, and the total must be exactly 100.
    """
    installed = list(installed)
    if not installed:
        raise HarnessError('no supported coding harness is installed')
    if not isinstance(shares, dict):
        raise HarnessError('shares must be an object')
    unknown = sorted(set(shares) - set(installed))
    if unknown:
        raise HarnessError(f'not installed or not supported: {", ".join(unknown)} '
                           f'(installed: {", ".join(installed)})')
    complete = {}
    for agent in installed:
        value = shares.get(agent, 0)
        if type(value) is not int or not 0 <= value <= 100:
            raise HarnessError(f'share for {agent} must be a whole number from 0 to 100')
        complete[agent] = value
    total = sum(complete.values())
    if total != 100:
        raise HarnessError(f'shares must add up to 100 (got {total})')
    return complete


def enabled_agents(plan: dict) -> list[str]:
    return sorted(agent for agent, share in plan['shares'].items() if share > 0)


# --------------------------------------------------------------------------- detection


def work_bands(registry: dict, agent: str) -> list[str]:
    """Bands in which ``agent`` has at least one automatically routable work model."""
    known = set(registry['agents'].get(agent, {}).get('model', {}).get('known_models', []))
    catalog = registry.get('model_catalog', {})
    policy = registry.get('model_routing', {}).get('non_complexity_band_policy', {})
    bands = []
    for band, entry in policy.items():
        models = set(entry.get('models', []))
        providers = set(entry.get('providers', []))
        models |= {model for model, meta in catalog.items() if meta.get('provider') in providers}
        for model in models & known:
            if catalog.get(model, {}).get('provider') == 'google' and agent != 'agy':
                continue
            bands.append(band)
            break
    from ai_skills.registry import BANDS
    return [band for band in BANDS if band in bands]


def detect(registry: dict, environment: dict | None = None, repo_root: Path | str | None = None) -> dict:
    """Describe every registered harness and the default split for usable ones."""
    from ai_skills.registry import BANDS
    from ai_skills.runner import bootstrap_path, detect_agents

    environment = bootstrap_path(os.environ if environment is None else environment)
    rows = detect_agents(registry, environment, Path(repo_root or Path.cwd()))
    harnesses, installed, notes = [], [], {}
    for name, display, status, reason in rows:
        bands = work_bands(registry, name)
        usable = status == 'detected' and bool(bands)
        if status == 'detected' and not bands:
            reason = 'installed, but no routable models are configured for it'
        harnesses.append({'agent': name, 'display_name': display, 'status': status, 'reason': reason,
                          'usable': usable, 'work_bands': bands})
        if usable:
            installed.append(name)
            if len(bands) < len(BANDS):
                notes[name] = f'{display}: only {", ".join(bands)} work with the current model list'
    return {'schema_version': SCHEMA_VERSION, 'harnesses': harnesses, 'installed': installed,
            'default_shares': default_shares(registry, installed), 'notes': notes}


# --------------------------------------------------------------------------- plan file


def plan_path(run_root: Path | str) -> Path:
    return Path(run_root).absolute() / RUN_DIRECTORY / PLAN_FILE


def coverage_gaps(registry: dict, shares: dict[str, int]) -> dict:
    """Report work bands or roles that have zero eligible models under ``shares``."""
    from ai_skills.registry import BANDS
    from ai_skills import runner

    enabled = [agent for agent, share in shares.items() if share > 0]
    covered_work: set[str] = set()
    for agent in enabled:
        covered_work.update(work_bands(registry, agent))
    missing_work_bands = [band for band in BANDS if band not in covered_work]

    # Check whether any enabled harness can run the read-only plan phase.
    # AGY requires a verified read-only profile; other enabled harnesses can plan
    # if they cover the bumped plan bands (medium..extreme).
    agy_can_plan = runner.resolve_agy_plan_profile(registry) is not None
    plan_harnesses = [a for a in enabled if a != 'agy' or agy_can_plan]
    plan_covered: set[str] = set()
    for agent in plan_harnesses:
        plan_covered.update(work_bands(registry, agent))
    plan_bands = ('medium', 'medium-hard', 'complex', 'extreme')
    missing_plan_bands = [band for band in plan_bands if band not in plan_covered]

    warnings = []
    if missing_work_bands:
        warnings.append(
            f'selected harnesses ({", ".join(sorted(enabled))}) have no configured models for bands: '
            f'{", ".join(missing_work_bands)}'
        )
    if missing_plan_bands and not missing_work_bands:
        warnings.append(
            f'selected harnesses ({", ".join(sorted(enabled))}) cannot run the pre-implementation plan phase '
            f'for bands: {", ".join(missing_plan_bands)} (planning will be skipped)'
        )
    return {
        'missing_work_bands': missing_work_bands,
        'missing_plan_bands': missing_plan_bands,
        'warnings': warnings,
    }


def build_plan(
    shares: dict[str, int],
    installed,
    source: str,
    *,
    registry: dict | None = None,
    allow_partial: bool = False,
) -> dict:
    if source not in SOURCES:
        raise HarnessError(f'source must be one of {", ".join(SOURCES)}')
    complete = validate_shares(shares, installed)
    warnings: list[str] = []
    if registry is not None:
        gaps = coverage_gaps(registry, complete)
        warnings = gaps['warnings']
        if gaps['missing_work_bands'] and not allow_partial:
            # Allow a partial default only if the installed set itself cannot cover all bands.
            installed_gaps = coverage_gaps(registry, {a: 1 for a in installed})
            if not installed_gaps['missing_work_bands']:
                raise HarnessError(
                    f'split cannot route all complexity bands (missing: {", ".join(gaps["missing_work_bands"])}); '
                    'give a positive share to a full-coverage harness or pass --allow-partial'
                )
    plan = {'schema_version': SCHEMA_VERSION, 'detected': sorted(installed), 'shares': complete,
            'source': source, 'created_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    if warnings:
        plan['warnings'] = warnings
    return plan


def validate_plan(plan) -> dict:
    if not isinstance(plan, dict) or plan.get('schema_version') != SCHEMA_VERSION:
        raise HarnessError('harness plan must be an object with schema_version 1')
    detected = plan.get('detected')
    if not isinstance(detected, list) or any(not isinstance(item, str) or not item for item in detected):
        raise HarnessError('harness plan detected must be a list of harness names')
    shares = plan.get('shares')
    if not isinstance(shares, dict) or set(shares) != set(detected):
        raise HarnessError('harness plan shares must cover exactly the detected harnesses')
    validate_shares(shares, detected)
    if plan.get('source') not in SOURCES:
        raise HarnessError('harness plan source is invalid')
    return plan


def load_plan(directory: Path | str) -> dict | None:
    """Load ``harness-plan.json`` from a run directory; ``None`` when absent."""
    from ai_skills.storage import load_json

    path = Path(directory) / PLAN_FILE
    try:
        plan = load_json(path, missing=None)
    except json.JSONDecodeError as error:
        raise HarnessError(f'invalid JSON in {path}: {error}') from error
    return None if plan is None else validate_plan(plan)


def save_plan(run_root: Path | str, plan: dict, *, force: bool = False) -> Path:
    from ai_skills.storage import write_json_atomic

    path = plan_path(run_root)
    if path.exists() and not force:
        raise HarnessError(f'{path} already exists; this run already has a harness split '
                           '(pass --force to replace it before any work starts)')
    write_json_atomic(path, validate_plan(plan))
    return path


# --------------------------------------------------------------------------- routing policy


def _reweight(table: dict, shares: dict[str, int], defaults: dict) -> dict[str, int]:
    weights = {}
    for agent, share in shares.items():
        if share <= 0:
            continue
        base = table.get(agent)
        default = defaults.get(agent)
        if not isinstance(base, (int, float)):
            weights[agent] = float(share)
        elif isinstance(default, (int, float)) and default > 0:
            weights[agent] = float(base) * share / float(default)
        else:
            weights[agent] = float(base)
    return round_to_100(weights) if weights else {}


def apply_plan(registry: dict, plan: dict) -> dict:
    """Return a copy of an expanded registry whose usage targets follow ``plan``.

    * Overall target (``default_target_percent``) becomes the user's split.
    * Every role/band table is restricted to enabled harnesses and reweighted by
      ``user share / default share``, then rescaled to 100. A harness missing
      from a table gets its user share; a table zero stays zero.
    """
    validate_plan(plan)
    agents = registry.get('agents', {})
    for agent in plan['shares']:
        if agent not in agents:
            raise HarnessError(f'harness plan names an unknown harness: {agent}')
    result = deepcopy(registry)
    distribution = result.setdefault('model_routing', {}).setdefault('usage_distribution', {})
    defaults = dict(distribution.get('default_target_percent', {}))
    shares = {agent: share for agent, share in plan['shares'].items() if share > 0}
    distribution['default_target_percent'] = dict(shares)
    for key in ('role_target_percent', 'non_complexity_band_target_percent'):
        tables = distribution.get(key, {})
        for name, table in list(tables.items()):
            if isinstance(table, dict):
                tables[name] = _reweight(table, shares, defaults)
    for role, bands in distribution.get('role_band_target_percent', {}).items():
        if isinstance(bands, dict):
            for band, table in list(bands.items()):
                if isinstance(table, dict):
                    bands[band] = _reweight(table, shares, defaults)
    return result


# --------------------------------------------------------------------------- CLI


def _load_registry(path):
    from ai_skills.registry import load_registry

    selected = path or os.environ.get('AGENT_REGISTRY_FILE') or None
    return load_registry(Path(selected) if selected else None)


def _describe(plan: dict, registry: dict) -> str:
    names = {agent: registry['agents'].get(agent, {}).get('display_name', agent) for agent in plan['shares']}
    parts = [f'{names[agent]} {share}%' for agent, share in sorted(plan['shares'].items(), key=lambda item: (-item[1], item[0]))]
    return ' · '.join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='ai-skills harness',
        description='Detect installed coding harnesses and save the per-run usage split.')
    commands = parser.add_subparsers(dest='action', required=True)
    detect_parser = commands.add_parser('detect', help='List supported harnesses and the default split.')
    detect_parser.add_argument('--repo-root', default=None)
    detect_parser.add_argument('--registry-file', default='')
    plan_parser = commands.add_parser('plan', help='Save the usage split for a run.')
    plan_parser.add_argument('--run-root', required=True)
    group = plan_parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--defaults', action='store_true', help='Use the default split for the installed harnesses.')
    group.add_argument('--shares', help='Comma-separated harness=percent values that add up to 100.')
    plan_parser.add_argument('--source', choices=SOURCES, default=None)
    plan_parser.add_argument('--allow-partial', action='store_true',
                             help='Allow a split that leaves some complexity bands uncovered.')
    plan_parser.add_argument('--force', action='store_true')
    plan_parser.add_argument('--registry-file', default='')
    show_parser = commands.add_parser('show', help='Print the saved split for a run.')
    show_parser.add_argument('--run-root', required=True)
    show_parser.add_argument('--registry-file', default='')
    args = parser.parse_args(argv)
    try:
        registry = _load_registry(args.registry_file)
        if args.action == 'detect':
            print(json.dumps(detect(registry, repo_root=args.repo_root), indent=2, sort_keys=True))
            return 0
        if args.action == 'show':
            plan = load_plan(plan_path(args.run_root).parent)
            if plan is None:
                print(f'error: no harness plan at {plan_path(args.run_root)}', file=sys.stderr)
                return 1
            print(json.dumps({**plan, 'summary': _describe(plan, registry)}, indent=2, sort_keys=True))
            return 0
        report = detect(registry, repo_root=args.run_root)
        installed = report['installed']
        if args.defaults:
            shares, source = report['default_shares'], args.source or 'default'
        else:
            shares, source = parse_shares(registry, args.shares), args.source or 'user'
        plan = build_plan(shares, installed, source, registry=registry, allow_partial=args.allow_partial)
        path = save_plan(args.run_root, plan, force=args.force)
        print(json.dumps({**plan, 'path': str(path), 'summary': _describe(plan, registry)}, indent=2, sort_keys=True))
        return 0
    except (HarnessError, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
