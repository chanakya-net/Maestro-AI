import json
from pathlib import Path
import shutil

import pytest

from ai_skills.resources import RESOURCE_NAMES, resource_path, resource_root
from test_cli import ROOT, invoke


def test_maintained_resources_match_the_checkout():
    assert resource_root().is_dir()
    assert set(RESOURCE_NAMES) == {p.name for p in (ROOT / "assets").iterdir()
                                   if p.suffix in {".md", ".json"}}
    for name in RESOURCE_NAMES:
        assert resource_path(name).read_bytes() == (ROOT / "assets" / name).read_bytes()


@pytest.mark.parametrize("name", ["../pyproject.toml", "/etc/passwd", "run-agent.sh", "missing.md"])
def test_resource_lookup_rejects_unmaintained_paths(name):
    with pytest.raises(ValueError, match="Unknown maintained resource"):
        resource_path(name)


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "invalid-schema", "empty-prompt", "missing-definitions", "invalid-definitions"])
def test_doctor_reports_broken_package_resources_without_writing(tmp_path, damage):
    package = tmp_path / "package" / "ai_skills"
    shutil.copytree(ROOT / "src" / "ai_skills", package, ignore=shutil.ignore_patterns("__pycache__"))
    data = package / "data"
    data.mkdir()
    for name in RESOURCE_NAMES:
        shutil.copyfile(ROOT / "assets" / name, data / name)
    if damage == "missing":
        (data / "prompt.md").unlink()
    elif damage == "empty-prompt":
        (data / "prompt.md").write_text("", encoding="utf-8")
    elif damage == "missing-definitions":
        (data / "agent-definitions.json").unlink()
    elif damage == "invalid-definitions":
        (data / "agent-definitions.json").write_text('{"schema_version": 999}', encoding="utf-8")
    else:
        (data / "agent-registry.json").write_text("{" if damage == "invalid-json" else '{"schema_version": 999}', encoding="utf-8")
    before = {str(p.relative_to(data)): p.read_bytes() for p in data.iterdir()}
    work = tmp_path / "work"
    work.mkdir()
    result = invoke("doctor", "--json", cwd=work, extra_env={"PYTHONPATH": str(package.parent)})
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["errors"]
    assert before == {str(p.relative_to(data)): p.read_bytes() for p in data.iterdir()}
    assert list(work.iterdir()) == []
