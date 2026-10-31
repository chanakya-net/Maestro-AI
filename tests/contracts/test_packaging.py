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
    environment = tmp_path / "environment"
    run(sys.executable, "-m", "venv", environment, cwd=tmp_path)
    bindir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    command = bindir / ("ai-skills.exe" if os.name == "nt" else "ai-skills")
    run(python, "-m", "pip", "install", "--no-deps", next(rebuilt.glob("*.whl")), cwd=tmp_path)
    work = tmp_path / "outside checkout"
    work.mkdir()
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
    env["PATH"] = ""
    for entrypoint in ([python, "-m", "ai_skills"], [command]):
        assert "doctor" in run(*entrypoint, "--help", cwd=work, env=env).stdout
        report = json.loads(run(*entrypoint, "doctor", "--json", cwd=work, env=env).stdout)
        assert report["ok"] is True
        assert set(report["resources"]) == set(RESOURCE_NAMES)
        assert report["version"] != "uninstalled (source checkout)"
        version = run(*entrypoint, "--version", cwd=work, env=env).stdout.strip()
        metadata = run(python, "-c", 'from importlib.metadata import version; print(version("ai-skills"))', cwd=work, env=env).stdout.strip()
        assert version == f"ai-skills {metadata}"
    assert list(work.iterdir()) == []
