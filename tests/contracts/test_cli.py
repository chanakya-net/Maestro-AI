"""The opt-in CLI must work before provider configuration or checkout setup."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def invoke(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = {key: value for key, value in os.environ.items()
           if key not in {"GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
    env.update(PATH="", PYTHONPATH=str(ROOT / "src"))
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "ai_skills", *args], cwd=cwd,
                          env=env, text=True, capture_output=True, timeout=15)


def test_help_without_provider_tools(tmp_path):
    result = invoke("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "doctor" in result.stdout
    assert "run-agent" not in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_unknown_command_is_rejected(tmp_path):
    result = invoke("does-not-exist", cwd=tmp_path)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_doctor_reports_interpreter_resources_and_no_writes(tmp_path):
    import json
    result = invoke("doctor", "--json", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert Path(report["interpreter"]) == Path(sys.executable)
    assert report["python_version"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}.")
    assert report["registry_schema_version"] == 1
    assert "prompt.md" in report["resources"]
    assert "agent-registry.json" in report["resources"]
    assert all(name.endswith((".md", ".json")) for name in report["resources"])
    assert list(tmp_path.iterdir()) == []


def test_version_reports_package_metadata_or_source_checkout(tmp_path):
    from importlib.metadata import PackageNotFoundError, version
    try:
        expected = version("ai-skills")
    except PackageNotFoundError:
        expected = "uninstalled (source checkout)"
    result = invoke("--version", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == f"ai-skills {expected}"
