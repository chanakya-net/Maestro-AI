"""Built artifacts work without a checkout and carry only maintained resources."""
import json
import os
from pathlib import Path
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
    environment = tmp_path / "environment"
    run(sys.executable, "-m", "venv", environment, cwd=tmp_path)
    bindir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    command = bindir / ("ai-skills.exe" if os.name == "nt" else "ai-skills")
    maestro = bindir / ("maestro.exe" if os.name == "nt" else "maestro")
    run(python, "-m", "pip", "install", "--no-deps", next(rebuilt.glob("*.whl")), cwd=tmp_path)
    work = tmp_path / "outside checkout"
    work.mkdir()
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI_SKILLS_RUN_ROOT"}}
    env["PATH"] = ""
    env["ASSETS_DEST"] = str(tmp_path / "absent-assets")
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


def test_installed_managed_payload_loads_shared_protocol_outside_checkout(distributions, tmp_path):
    wheel=next(distributions.glob('*.whl'))
    target=tmp_path/'installed'
    run(sys.executable,'-m','pip','install','--no-deps','--target',target,wheel,cwd=tmp_path)
    work=tmp_path/'unrelated';work.mkdir()
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
    env=dict(os.environ,PYTHONPATH=str(target))
    run(sys.executable,'-c',script,cwd=work,env=env)


def test_installed_catalog_defaults_usage_and_effort_contract(distributions, tmp_path):
    wheel = next(distributions.glob("*.whl"))
    target = tmp_path / "installed-contract"
    run(sys.executable, "-m", "pip", "install", "--no-deps", "--target", target,
        wheel, cwd=tmp_path)
    work = tmp_path / "installed-contract-work"
    work.mkdir()
    source_registry = ROOT / "assets" / "agent-registry.json"
    script = r'''import json
from pathlib import Path
from ai_skills.effort import resolve_effort
from ai_skills.registry import load_registry
from ai_skills.resources import resource_path
from ai_skills.runner import AgentRunConfig, build_invocation
from ai_skills.routing import RouteRequest, select_route

source = json.loads(Path(__import__('os').environ['SOURCE_REGISTRY']).read_text())
bundled = json.loads(resource_path('agent-registry.json').read_text())
registry = load_registry()
definitions = json.loads(resource_path('agent-definitions.json').read_text())
expected = {
    *(f'gemini-4-argon-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'gemini-3.8-flash-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-opus-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-sonnet-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    'gpt-oss-120b-medium',
}
catalog = bundled['model_catalog']
assert {name for name, model in catalog.items() if 'agy' in model.get('agents', [])} == expected
assert catalog['gemini-3.8-flash-low']['work_bands'] == ['quite-easy']
assert catalog['gemini-3.8-flash-medium']['work_bands'] == ['easy']
assert catalog['gemini-3.8-flash-medium']['complexity_bands'] == [
    'quite-easy', 'easy', 'medium', 'medium-hard', 'complex', 'holy-fuck']
assert sum(model.get('explicit_only') is True for model in catalog.values()) == 11
assert definitions['agents']['agy']['model']['default'] == 'gemini-3.8-flash-high'
assert definitions['agents']['agy']['model']['flag_template'] == '--model {{model}}'
for band, model in (('quite-easy', 'gemini-3.8-flash-low'),
                    ('easy', 'gemini-3.8-flash-medium')):
    route = select_route(RouteRequest('impl', band, frozenset({'agy'}), forced_agent='agy'),
                         registry, {})
    assert (route.agent, route.model) == ('agy', model)
for band in ('quite-easy', 'easy', 'medium', 'medium-hard', 'complex', 'holy-fuck'):
    route = select_route(RouteRequest('complexity', band, frozenset({'agy'}), forced_agent='agy'),
                         registry, {})
    assert (route.agent, route.model) == ('agy', 'gemini-3.8-flash-medium')
assert bundled['model_routing']['usage_distribution'] == source['model_routing']['usage_distribution']
assert registry['agents']['claude']['model']['known_models'] == [
    'claude-haiku-5-5', 'claude-opus-5-5', 'claude-fable-5-1', 'claude-sonnet-5-5']
assert registry['agents']['codex']['model']['known_models'] == [
    'gpt-6-astra', 'gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol']
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
                                 ('gemini-4-argon-high', 'gemini-4-argon-high')):
    config = AgentRunConfig(agent='agy', model=selected, repo_root=Path.cwd(),
        context_file=Path('context.md'), prompt_file=Path('prompt.md'),
        environment={}, unattended=True, gui_mode='0')
    argv = build_invocation(config, registry).argv
    model_flag = argv.index('--model')
    assert argv[model_flag + 1] == expected_model
    assert argv[-4:] == ('--input-format', 'stream-json', '--output-format', 'stream-json')
'''
    env = dict(os.environ, PYTHONPATH=str(target), SOURCE_REGISTRY=str(source_registry))
    env.pop("AI_SKILLS_RUN_ROOT", None)
    run(sys.executable, "-c", script, cwd=work, env=env)


def test_installed_cli_exposes_explicit_planning_and_role_aware_effort(distributions, tmp_path):
    wheel = next(distributions.glob("*.whl"))
    target = tmp_path / "installed-cli"
    run(sys.executable, "-m", "pip", "install", "--no-deps", "--target", target,
        wheel, cwd=tmp_path)
    work = tmp_path / "installed-cli-work"
    work.mkdir()
    context = work / "context.md"
    prompt = work / "prompt.md"
    context.write_text("Plan a task", encoding="utf-8")
    prompt.write_text("Do work", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(target), PATH="", REPO_ROOT=str(work),
               ASSETS_DEST=str(work / "absent-assets"))
    env.pop("AI_SKILLS_RUN_ROOT", None)
    for role, expected in ((None, "high"), ("plan", "xhigh"), ("review", "high")):
        args = ["agent", "run", "--agent", "codex-cli", "--effort", "maximum",
                "--context-file", context, "--prompt-file", prompt,
                "--unattended", "--dry-run"]
        if role:
            args += ["--role", role]
        result = run(sys.executable, "-m", "ai_skills", *args, cwd=work, env=env)
        assert f"model_reasoning_effort={expected}" in result.stdout
