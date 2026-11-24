"""Module facade helper for split subpackages.

Allows a former monolithic module (such as ``dispatch.py`` or ``state.py``) to
be split into cohesive submodules under a package while preserving full
attribute and ``monkeypatch.setattr(pkg, ...)`` compatibility across submodules.
"""
from __future__ import annotations

import sys
import types


class _PackageFacade(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__") and name.endswith("__"):
            return
        for mod in self.__dict__.get("_submodules", ()):
            if name in mod.__dict__:
                setattr(mod, name, value)

    def __delattr__(self, name: str) -> None:
        super().__delattr__(name)
        for mod in self.__dict__.get("_submodules", ()):
            if name in mod.__dict__:
                delattr(mod, name)


def install_facade(package_name: str, submodules: tuple[types.ModuleType, ...]) -> None:
    """Re-export symbols from ``submodules`` onto ``package_name`` and propagate patches."""
    current = sys.modules[package_name]
    current.__dict__["_submodules"] = tuple(submodules)
    for mod in submodules:
        for key, value in mod.__dict__.items():
            if key.startswith("__") and key.endswith("__"):
                continue
            current.__dict__.setdefault(key, value)
    current.__class__ = _PackageFacade
