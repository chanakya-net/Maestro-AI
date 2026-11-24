"""Tests for the temporary subpackage facade bridge and detached import root."""
from __future__ import annotations

from pathlib import Path
import sys
import types

from ai_skills._facade import install_facade
from ai_skills.dispatch import adoption


def test_package_facade_propagates_setattr_and_delattr_to_submodules(monkeypatch):
    pkg = types.ModuleType("ai_skills._test_facade_pkg")
    sub = types.ModuleType("ai_skills._test_facade_pkg.sub")
    sub.VALUE = "original"
    sub.helper = lambda: sub.VALUE
    monkeypatch.setitem(sys.modules, pkg.__name__, pkg)
    monkeypatch.setitem(sys.modules, sub.__name__, sub)

    install_facade(pkg.__name__, (sub,))
    assert pkg.VALUE == "original"
    assert pkg.helper() == "original"

    pkg.VALUE = "patched"
    assert sub.VALUE == "patched"
    assert pkg.helper() == "patched"

    del pkg.VALUE
    assert not hasattr(sub, "VALUE")


def test_detached_dispatcher_import_root_points_to_package_parent():
    root = Path(adoption.__file__).resolve().parents[2]
    assert (root / "ai_skills" / "__init__.py").is_file()
