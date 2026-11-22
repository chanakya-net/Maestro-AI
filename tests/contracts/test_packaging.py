"""Built artifacts work without a checkout and carry only maintained resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

from ai_skills.resources import RESOURCE_NAMES

ROOT = Path(__file__).resolve().parents[2]


def run(*argv, cwd, env=None):
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                            text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    output = tmp_path_factory.mktemp("distributions")
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", output, cwd=ROOT)
    return output


def isolated_environment(work, target=None, **overrides):
    platform_keys = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR"}
    env = {key: value for key, value in os.environ.items() if key.upper() in platform_keys}
    env.update(HOME=str(work), USERPROFILE=str(work), PATH="", PYTHONNOUSERSITE="1",
               ASSETS_DEST=str(work / "absent-assets"), RUN_AGENT_BOOTSTRAP_PATH="0",
               RUN_WITH_IT_GITHUB_UPDATES="0", PIP_CONFIG_FILE=os.devnull)
    if target is not None:
        env["PYTHONPATH"] = str(target)
    return {**env, **overrides}


@pytest.fixture
def installed_wheel(distributions, tmp_path):
    target = tmp_path / "installed"
    work = tmp_path / "outside-checkout"
    work.mkdir()
    assert not any((parent / ".run-with-it").exists() for parent in (work, *work.parents))
    env = isolated_environment(work, target)
    run(sys.executable, "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--target", target,
        next(distributions.glob("*.whl")), cwd=work, env=isolated_environment(work))
    run(sys.executable, "-c", "import ai_skills, sys; from pathlib import Path; "
        "assert Path(ai_skills.__file__).is_relative_to(Path(sys.argv[1]))",
        target, cwd=work, env=env)
    return target, work, env



def test_built_artifacts_exclude_legacy_executables_and_generated_docs(distributions):
    wheel = next(distributions.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert {name.removeprefix("ai_skills/data/") for name in names
                if name.startswith("ai_skills/data/")} == set(RESOURCE_NAMES)
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        assert not any(name.endswith((".sh", ".ps1")) or "/docs/" in name or ".superpowers" in name
                       for name in names)
    with tarfile.open(next(distributions.glob("*.tar.gz"))) as archive:
        members = [name.partition("/")[2] for name in archive.getnames()]
        assert "LICENSE" in members
        assert "examples/add_two_numbers.py" in members
        assert "explainer.html" in members
        assert "tests/fixtures/legacy_states/main-state-v4.json" in members
        assert not any(name.startswith(("docs/", ".superpowers/", ".run-with-it/", ".pair-colleague/"))
                       or name.endswith((".sh", ".ps1")) for name in members)
        assert {name.removeprefix("assets/") for name in members if name.startswith("assets/")} == set(RESOURCE_NAMES)


def test_sdist_rebuild_and_installed_entrypoints_outside_checkout(distributions, tmp_path):
    extracted = tmp_path / "extracted"
    with tarfile.open(next(distributions.glob("*.tar.gz"))) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    assert not (source / ".git").exists()
    rebuilt = tmp_path / "rebuilt"
    run(sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", rebuilt, cwd=source)
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as archive:
        entrypoints = archive.read(next(name for name in archive.namelist() if name.endswith("/entry_points.txt"))).decode()
        assert "ai-skills = ai_skills.cli:main" in entrypoints
        assert "maestro = ai_skills.cli:main" in entrypoints
    work = tmp_path / "outside checkout"
    work.mkdir()
    env = isolated_environment(work)
    environment = tmp_path / "environment"
    run(sys.executable, "-I", "-m", "venv", environment, cwd=work, env=env)
    bindir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    command = bindir / ("ai-skills.exe" if os.name == "nt" else "ai-skills")
    maestro = bindir / ("maestro.exe" if os.name == "nt" else "maestro")
    run(python, "-I", "-m", "pip", "install", "--no-index", "--no-deps",
        next(rebuilt.glob("*.whl")), cwd=work, env=env)
    for entrypoint in ([python, "-m", "ai_skills"], [command], [maestro]):
        assert "doctor" in run(*entrypoint, "--help", cwd=work, env=env).stdout
        report = json.loads(run(*entrypoint, "doctor", "--json", cwd=work, env=env).stdout)
        assert report["ok"] is True
        assert set(report["resources"]) == set(RESOURCE_NAMES)
        assert report["version"] != "uninstalled (source checkout)"
        version = run(*entrypoint, "--version", cwd=work, env=env).stdout.strip()
        metadata = run(python, "-c", 'from importlib.metadata import version; print(version("ai-skills"))', cwd=work, env=env).stdout.strip()
        assert version == f"ai-skills {metadata}"
    assert list(work.iterdir()) == []


def test_installed_managed_payload_loads_shared_protocol_outside_checkout(installed_wheel):
    _, work, env = installed_wheel
    script='''from pathlib import Path
from ai_skills.runner import AgentRunConfig, _payload
from ai_skills.resources import resource_path
Path('context.md').write_text('task')
Path('prompt.md').write_text('role')
config=AgentRunConfig(agent='fixture',model='fixture',repo_root=Path.cwd(),prompt_file=Path('prompt.md'),context_file=Path('context.md'),state_file=Path('worker.state.json'),result_file=Path('result.json'),role='impl',issue='7',run_id='run-7',attempt_id='attempt-1',dispatcher_managed=True)
payload=_payload(config)
assert payload.count('# Managed artifact protocol')==1
assert 'artifact_kind="role_failure"' in payload
assert resource_path('artifact-protocol.md').is_file()
assert 'Authoritative attempt metadata:' in payload
assert 'run-7' in payload
'''
    run(sys.executable,'-c',script,cwd=work,env=env)


def test_installed_catalog_defaults_usage_and_effort_contract(installed_wheel):
    target, work, env = installed_wheel
    models = run(sys.executable, "-m", "ai_skills", "agent", "models", "agy",
                 cwd=work, env=env).stdout.splitlines()
    run(sys.executable, "-c", CATALOG_PROBE, target, *models, cwd=work, env=env)
    source = json.loads((ROOT / "assets" / "agent-registry.json").read_text(encoding="utf-8"))
    bundled = json.loads((target / "ai_skills" / "data" / "agent-registry.json").read_text(encoding="utf-8"))
    assert bundled["model_routing"]["usage_distribution"] == source["model_routing"]["usage_distribution"]


CATALOG_PROBE = r'''import json
import sys
from pathlib import Path
import ai_skills
from ai_skills.effort import resolve_effort
from ai_skills.registry import load_registry
from ai_skills.resources import resource_path
from ai_skills.runner import AgentRunConfig, build_invocation
from ai_skills.routing import RouteRequest, select_route

target = Path(sys.argv[1])
assert Path(ai_skills.__file__).is_relative_to(target)
for name in ('agent-registry.json', 'agent-definitions.json'):
    assert Path(str(resource_path(name))).is_relative_to(target)
bundled = json.loads(resource_path('agent-registry.json').read_text())
registry = load_registry()
definitions = json.loads(resource_path('agent-definitions.json').read_text())
expected = {
    *(f'gemini-4-argon-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'gemini-4-{family}-{effort}' for family in ('neon', 'hydrogen') for effort in ('low', 'medium', 'high', 'xhigh')),
    *(f'gemini-4-carbon-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'gemini-3.8-flash-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-opus-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-sonnet-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    'gpt-oss-120b-medium',
}
assert len(expected) == len(sys.argv[2:]) == 24
assert set(sys.argv[2:]) == expected
assert sum(model.startswith('gemini-4-') for model in expected) == 14
catalog = bundled['model_catalog']
assert {name for name, model in catalog.items() if 'agy' in model.get('agents', [])} == expected
assert catalog['gemini-3.8-flash-low']['work_bands'] == ['quite-easy']
assert catalog['gemini-3.8-flash-medium']['work_bands'] == ['easy']
assert catalog['gemini-3.8-flash-medium']['complexity_bands'] == [
    'quite-easy', 'easy', 'medium', 'medium-hard', 'complex', 'holy-fuck']
assert sum(model.get('explicit_only') is True for model in catalog.values()) == 22
assert definitions['agents']['agy']['model']['default'] == 'gemini-3.8-flash-high'
assert definitions['agents']['agy']['model']['flag_template'] == '--model {{model}}'
matrix = {
    'quite-easy': {'gemini-3.8-flash-low'},
    'easy': {'gemini-3.8-flash-medium'},
    'medium': {'gemini-4-hydrogen-medium'},
    'medium-hard': {'gemini-4-argon-high', 'gemini-4-hydrogen-high'},
    'complex': {'gemini-4-neon-high'},
    'holy-fuck': {'gemini-4-neon-high'},
}
agy = frozenset({'agy'})
for band, candidates in matrix.items():
    route = select_route(RouteRequest('impl', band, agy), registry, {})
    assert route.agent == 'agy' and route.model in candidates
    assert {c['model'] for c in route.selection['evaluated_candidates']} == candidates
    scorer = select_route(RouteRequest('complexity', band, agy), registry, {})
    assert (scorer.agent, scorer.model) == ('agy', 'gemini-3.8-flash-medium')
    assert {c['model'] for c in scorer.selection['evaluated_candidates']} == {'gemini-3.8-flash-medium'}
positive_target = select_route(RouteRequest('impl', 'medium', agy | {'codex', 'claude'}), registry, {})
assert positive_target.agent in {'codex', 'claude'} and positive_target.target_percent > 0
sibling = select_route(RouteRequest('impl', 'medium-hard', agy,
    exclude_models={'gemini-4-argon-high'}), registry, {})
assert sibling.model == 'gemini-4-hydrogen-high'
carbon = select_route(RouteRequest('impl', 'medium', agy,
    forced_model='gemini-4-carbon-medium'), registry, {})
assert (carbon.agent, carbon.model) == ('agy', 'gemini-4-carbon-medium')
assert registry['agents']['claude']['model']['known_models'] == [
    'claude-haiku-5-5', 'claude-opus-5-5', 'claude-fable-5-2', 'claude-sonnet-5-5']
assert registry['agents']['codex']['model']['known_models'] == [
    'gpt-6.2-astra', 'gpt-6.1-terra', 'gpt-6.2-sol']
for token in ('low', 'medium', 'high', 'xhigh', 'max', 'maximum'):
    assert resolve_effort('codex', token, 'impl', registry).effective == 'high'
    assert resolve_effort('codex', token, 'plan', registry).effective == 'xhigh'
assert resolve_effort('claude', 'maximum', 'impl', registry).effective == 'high'
assert resolve_effort('claude', 'maximum', 'impl', registry).requested == 'maximum'
assert resolve_effort('claude', 'medium', 'plan', registry).effective == 'medium'
assert resolve_effort('claude', 'maximum', 'plan', registry).effective == 'xhigh'
assert resolve_effort('agy', 'maximum', 'plan', registry).effective == 'maximum'
for agent in ('codex', 'claude'):
    assert registry['agents'][agent]['permission_modes']['read_only']
Path('context.md').write_text('task')
Path('prompt.md').write_text('instructions')
for selected, expected_model in (('', 'gemini-3.8-flash-high'),
                                 ('gemini-4-argon-high', 'gemini-4-argon-high'),
                                 ('gemini-4-carbon-medium', 'gemini-4-carbon-medium')):
    config = AgentRunConfig(agent='agy', model=selected, repo_root=Path.cwd(),
        context_file=Path('context.md'), prompt_file=Path('prompt.md'),
        environment={}, unattended=True, gui_mode='0')
    argv = build_invocation(config, registry).argv
    model_flag = argv.index('--model')
    assert argv[model_flag + 1] == expected_model
    assert argv[-4:] == ('--input-format', 'stream-json', '--output-format', 'stream-json')
'''

def test_installed_cli_exposes_explicit_planning_and_role_aware_effort(installed_wheel):
    _, work, env = installed_wheel
    context = work / "context.md"
    prompt = work / "prompt.md"
    context.write_text("Plan a task", encoding="utf-8")
    prompt.write_text("Do work", encoding="utf-8")
    env = {**env, "REPO_ROOT": str(work)}
    for role, expected in ((None, "high"), ("plan", "xhigh"), ("review", "high")):
        args = ["agent", "run", "--agent", "codex-cli", "--effort", "maximum",
                "--context-file", context, "--prompt-file", prompt,
                "--unattended", "--dry-run"]
        if role:
            args += ["--role", role]
        result = run(sys.executable, "-m", "ai_skills", *args, cwd=work, env=env)
        assert f"model_reasoning_effort={expected}" in result.stdout


def test_installed_pair_purpose_defaults_and_rejects_unknown_values(installed_wheel):
    _, work, env = installed_wheel
    task = work / "task.md"
    task.write_text("Plan a task", encoding="utf-8")
    harness_bin = work / "harness-bin"
    harness_bin.mkdir()
    extension = ".cmd" if os.name == "nt" else ""
    for harness in ("codex", "claude"):
        executable = harness_bin / f"{harness}{extension}"
        executable.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n",
                              encoding="utf-8")
        if os.name != "nt":
            executable.chmod(0o755)
    env = {**env, "PATH": str(harness_bin), "REPO_ROOT": str(work)}
    lineup = ["--agent-1-harness", "codex", "--agent-1-model", "gpt-6.2-sol", "--agent-1-effort", "high",
              "--agent-2-harness", "claude", "--agent-2-model", "claude-sonnet-5-5", "--agent-2-effort", "medium"]
    for purpose, expected, effort in ((None, "general", "high"), ("plan", "plan", "xhigh")):
        runs = work / f"runs-{expected}"
        args = ["pair", "start", task, "--run-root", runs, *lineup]
        if purpose:
            args += ["--purpose", purpose]
        run(sys.executable, "-m", "ai_skills", *args, cwd=work, env=env)
        (config_file,) = runs.glob("*/config.json")
        config = json.loads(config_file.read_text(encoding="utf-8"))
        assert config["purpose"] == expected
        assert config["agents"]["1"]["effort"] == effort
    rejected_runs = work / "runs-rejected"
    rejected = subprocess.run([sys.executable, "-m", "ai_skills", "pair", "start", str(task),
                               "--purpose", "planning", "--run-root", str(rejected_runs), *lineup],
                              cwd=work, env=env, text=True, capture_output=True, timeout=120)
    assert rejected.returncode == 2 and "invalid choice: 'planning'" in rejected.stderr
    assert not rejected_runs.exists()


def test_installed_new_pool_deadline_defaults(installed_wheel, tmp_path):
    from pool_support import pool_fixture

    target, work, env = installed_wheel
    run_root = tmp_path / "new-run"
    run_root.mkdir()
    setup = pool_fixture(run_root, {1: {}}, parallel=1)
    (maestro,) = target.rglob("maestro.exe" if os.name == "nt" else "maestro")
    result = run(maestro, "run", "pool", "--state-file", setup.state_file, "--dry-run",
                 cwd=work, env=env)
    (worker,) = (json.loads(line) for line in result.stdout.splitlines() if line.startswith("{"))
    assert worker["agent_run"]["role"] == "sub-coord"
    assert (worker["timeout_seconds"], worker["hard_limit_seconds"]) == (0, 0)
    assert (worker["quiet_seconds"], worker["stall_seconds"]) == (900, 1200)

    run_dir = setup.state_file.parent
    engine = json.loads((run_dir / "engine.json").read_text(encoding="utf-8"))
    lease = json.loads((run_dir / "main" / "pool.state.json").read_text(encoding="utf-8"))
    assert engine["engine"] == "python" and Path(engine["python"]) == Path(sys.executable)
    assert lease["state"] == "dry-run"
    config = lease["config"]
    assert (config["timeout_seconds"], config["hard_limit_seconds"], config["max_worker_wait_seconds"]) == (0, 0, 0)
    run(sys.executable, "-c", POOL_WORKER_PROBE, target, json.dumps(config), cwd=work, env=env)


POOL_WORKER_PROBE = """
import json
import sys
from dataclasses import replace
from pathlib import Path
from ai_skills import pool
from ai_skills.dispatch import WorkerConfig

assert Path(pool.__file__).is_relative_to(Path(sys.argv[1]))
supervisor = pool.PoolSupervisor(pool.PoolConfig.from_record(json.loads(sys.argv[2])))
recovery = supervisor.worker('1', kind='merge-recovery')
assert (recovery.timeout_seconds, recovery.hard_limit_seconds) == (0, 0)
assert (recovery.quiet_seconds, recovery.stall_seconds) == (900, 1200)
child = WorkerConfig(replace(recovery.agent_run, role='impl'), recovery.state_file)
assert (child.timeout_seconds, child.hard_limit_seconds) == (0, 7200)
assert (child.quiet_seconds, child.stall_seconds) == (900, 1200)
"""


@pytest.fixture
def marker_harnesses(installed_wheel):
    _, work, env = installed_wheel
    bin_dir = work / "harness-bin"
    bin_dir.mkdir()
    for name in ("agy", "codex"):
        marker = work / f"{name}-invoked"
        if os.name == "nt":
            harness = bin_dir / f"{name}.cmd"
            harness.write_text(f'@echo --model --input-format --output-format --read-only\n@echo invoked>"{marker}"\n',
                               encoding="utf-8")
        else:
            harness = bin_dir / name
            harness.write_text(f"#!/bin/sh\necho --model --input-format --output-format --read-only\n"
                               f"echo invoked > '{marker}'\n", encoding="utf-8")
            harness.chmod(0o755)
        assert shutil.which(name, path=str(bin_dir))
    return work, {**env, "PATH": str(bin_dir), "PAIR_COLLEAGUE_ALLOW_UNVERIFIED_SAFE": "1"}


def rejected_cli(*args, work, env):
    result = subprocess.run([sys.executable, "-m", "ai_skills", *map(str, args)],
                            cwd=work, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, result.stdout + result.stderr
    return result.stderr


@pytest.mark.parametrize("purpose", ["general", "plan"])
def test_installed_agy_requires_verified_read_only(marker_harnesses, purpose):
    work, env = marker_harnesses
    model = "gemini-4-neon-high"
    route = ["route", "--role", "plan", "--complexity-level", "medium-hard",
             "--detected-agents", "agy,codex", "--ledger-file", work / "ledger.json"]
    error = rejected_cli(*route, "--forced-agent", "agy", work=work, env=env)
    assert "no compatible routing candidates role=plan level=holy-fuck" in error
    alternate = run(sys.executable, "-m", "ai_skills", *route, cwd=work, env=env)
    assert json.loads(alternate.stdout)["agent"] == "codex"

    task = work / "task.md"
    task.write_text("Plan a fixture task", encoding="utf-8")
    config = work / "dispatch.json"
    config.write_text(json.dumps({
        "agent": "agy", "model": model, "role": "plan", "issue": "7",
        "repo_root": str(work), "issue_dir": str(work), "context_file": str(task),
        "prompt_file": str(task), "state_file": str(work / "worker.state.json"),
        "result_file": str(work / "plan-status.json"), "permission_mode": "safe",
        "unattended": True,
    }), encoding="utf-8")
    assert "agy-plan-read-only-unavailable" in rejected_cli(
        "worker", "dispatch", "--config", config, work=work, env=env)
    runs = work / "pairs"
    error = rejected_cli("pair", "start", task, "--purpose", purpose, "--run-root", runs,
                         "--agent-1-harness", "agy", "--agent-1-model", model, "--agent-1-effort", "high",
                         "--agent-2-harness", "codex", "--agent-2-model", "gpt-6.2-sol",
                         "--agent-2-effort", "high", work=work, env=env)
    assert "agent 1" in error and model in error and "verified read-only" in error
    assert not list(runs.glob("*/config.json"))
    for filename in ("agy-invoked", "codex-invoked", "worker.state.json", "plan-status.json", "plan.md"):
        assert not (work / filename).exists()
