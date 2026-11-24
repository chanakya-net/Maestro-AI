"""Unit tests for zero-clone installs from an installed ai_skills package (uv tool / pipx)."""
from pathlib import Path
import shutil
import zipfile

import pytest

from ai_skills import installation
from ai_skills.installation import plan as plan_mod, runtime as runtime_mod
from ai_skills.resources import RESOURCE_NAMES, SKILL_NAMES

REPO_ROOT = Path(__file__).parents[2]


def _stage_installed_package(tmp_path: Path, version: str = "0.1.0.dev0") -> Path:
    site = tmp_path / "site-packages"
    pkg = site / "ai_skills"
    shutil.copytree(REPO_ROOT / "src" / "ai_skills", pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    data_dir = pkg / "data"
    data_dir.mkdir(parents=True)
    for name in RESOURCE_NAMES:
        shutil.copy2(REPO_ROOT / "assets" / name, data_dir / name)
    skills_dir = pkg / "skills"
    for name in SKILL_NAMES:
        dst = skills_dir / name
        dst.mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "skills" / name / "SKILL.md", dst / "SKILL.md")
    dist_info = site / f"ai_skills-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: ai-skills\nVersion: {version}\nRequires-Dist: psutil>=7\n",
        encoding="utf-8",
    )
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nai-skills = ai_skills.cli:main\nmaestro = ai_skills.cli:main\n",
        encoding="utf-8",
    )
    return pkg


def test_is_installed_package_and_source_hashing(tmp_path):
    assert plan_mod._is_installed_package(REPO_ROOT) is False
    pkg = _stage_installed_package(tmp_path)
    assert plan_mod._is_installed_package(pkg) is True

    source, version, deps, digest = plan_mod._source(pkg)
    assert source == pkg.absolute()
    assert version == "0.1.0.dev0"
    assert "psutil>=7" in deps
    assert len(digest) == 64

    # Missing a maintained resource fails closed.
    (pkg / "data" / RESOURCE_NAMES[0]).unlink()
    with pytest.raises(ValueError, match="missing maintained package file"):
        plan_mod._source(pkg)


def test_repack_installed_wheel_produces_valid_installable_wheel(tmp_path):
    pkg = _stage_installed_package(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    wheel = runtime_mod._repack_installed_wheel(pkg, "0.1.0.dev0", out_dir)
    assert wheel.is_file() and wheel.suffix == ".whl"

    # The repacked wheel must itself pass _source(wheel) validation!
    src, version, deps, digest = plan_mod._source(wheel)
    assert src == wheel.absolute()
    assert version == "0.1.0.dev0"
    assert "psutil>=7" in deps
    assert len(digest) == 64

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        assert "ai_skills-0.1.0.dev0.dist-info/METADATA" in names
        assert "ai_skills-0.1.0.dev0.dist-info/WHEEL" in names
        assert "ai_skills-0.1.0.dev0.dist-info/entry_points.txt" in names
        assert "ai_skills-0.1.0.dev0.dist-info/RECORD" in names
        assert "ai_skills/cli.py" in names
        assert "ai_skills/dispatch/__init__.py" in names
        assert "ai_skills/issue.py" in names
        assert "maestro = ai_skills.cli:main" in zf.read("ai_skills-0.1.0.dev0.dist-info/entry_points.txt").decode()


def test_plan_install_accepts_installed_package_without_checkout(tmp_path):
    pkg = _stage_installed_package(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    plan = installation.plan_install(
        installation.InstallConfig(source=pkg, assets_dest=tmp_path / "assets", home=home, agents=("codex",), dry_run=True)
    )
    assert plan.version == "0.1.0.dev0"
    assert plan.source == pkg.absolute()
    assert any("--no-deps" in cmd and str(pkg.absolute()) in cmd for cmd in plan.pip_commands)
