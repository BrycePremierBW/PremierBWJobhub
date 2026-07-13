"""Dependency binder for extracted modules.

The original app used one global namespace. During the staged refactor, this binder
shares service symbols between modules without reintroducing import cycles.
"""
from __future__ import annotations

from types import ModuleType
from typing import Iterable


def bind_modules(modules: Iterable[ModuleType]) -> dict:
    namespace = {}
    for module in modules:
        for name, value in vars(module).items():
            if not name.startswith("__"):
                namespace[name] = value

    for module in modules:
        vars(module).update(namespace)

    return namespace
