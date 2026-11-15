"""Plugin discovery: every module in this package is imported, so dropping a file in is enough."""

from __future__ import annotations

import importlib
import pkgutil

from sections.base import REGISTRY, SectionPlugin, register

__all__ = ["REGISTRY", "SectionPlugin", "load_plugins", "register"]


def load_plugins() -> dict[str, type[SectionPlugin]]:
    for mod in pkgutil.iter_modules(__path__):
        if mod.name != "base":
            importlib.import_module(f"{__name__}.{mod.name}")
    return REGISTRY
