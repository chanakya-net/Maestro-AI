"""The opt-in CLI must work before provider configuration or checkout setup."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def invoke(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = {key: value for key, value in os.environ.items()
           if key not in {"GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI_SKILLS_RUN_ROOT"}}
    env.update(PATH="", PYTHONPATH=str(ROOT / "src"), ASSETS_DEST=str(cwd / "absent-assets"))
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
    assert report["registry_schema_version"] == 2
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


def test_launcher_delegates_to_current_managed_runtime(tmp_path):
    import json
    assets = tmp_path / "assets"
    assets.mkdir()
    shadow = tmp_path / "shadow" / "ai_skills"
    shadow.mkdir(parents=True)
    (shadow / "__main__.py").write_text("raise RuntimeError('shadow loaded')")
    for name in ("first", "second"):
        envdir = tmp_path / (name + " environment")
        subprocess.run([sys.executable, "-m", "venv", str(envdir)], check=True)
        python = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
        package = Path(site) / "ai_skills"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "__main__.py").write_text(
            "import json, sys\n" + f"print(json.dumps([{name!r}, sys.argv[1:], sys.flags.isolated]))\n" + "sys.exit(17)\n")
        (assets / "runtime.json").write_text(json.dumps({"schema_version": 1, "python": str(python)}))
        result = invoke("future-command", "argument with spaces", cwd=tmp_path,
                        extra_env={"ASSETS_DEST": str(assets), "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(shadow.parent)})
        assert result.returncode == 17, result.stderr
        assert json.loads(result.stdout) == [name, ["future-command", "argument with spaces"], 1]


def test_selected_interpreter_does_not_delegate(tmp_path, monkeypatch):
    import json
    from ai_skills import cli
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "runtime.json").write_text(json.dumps({"schema_version": 1, "python": sys.executable}))
    monkeypatch.setenv("ASSETS_DEST", str(assets))
    monkeypatch.delenv("AI_SKILLS_RUN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.runtime_command(["agent", "list"]) is None


def test_bootstrap_commands_remain_local(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "runtime.json").write_text("not json")
    env = {"ASSETS_DEST": str(assets)}
    for args in ((), ("--help",), ("--version",), ("doctor", "--json"),
                 ("install", "--list"), ("uninstall", "--list"), ("update", "--help")):
        result = invoke(*args, cwd=tmp_path, extra_env=env)
        assert result.returncode == 0, result.stderr
