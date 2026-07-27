"""Discovers Tool subclasses under ``chipagent/tools/`` via importlib.

Mirrors :class:`chipagent.skills.loader.SkillLoader`: each ``<name>.py``
module (except ``base``/``loader``/``__init__``) is imported and scanned for
``Tool`` subclasses, which are instantiated with no args. Adding a Tool is
therefore "drop a .py file" — zero registration code.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import List, Optional

from .base import Tool

_EXCLUDE = {"base", "loader", "__init__"}


class ToolLoader:
    def __init__(self, package: str = "chipagent.tools") -> None:
        self._package = package

    def discover(self) -> List[Tool]:
        tools: List[Tool] = []
        try:
            pkg = importlib.import_module(self._package)
        except ModuleNotFoundError:
            return tools
        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path is None:
            return tools
        for mod_info in pkgutil.iter_modules(pkg_path):
            name = mod_info.name
            if name in _EXCLUDE:
                continue
            try:
                module = importlib.import_module(f"{self._package}.{name}")
            except Exception:
                continue
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Tool)
                    and attr is not Tool
                    and attr.__module__ == module.__name__
                ):
                    try:
                        tools.append(attr())
                    except Exception:
                        continue
        return tools

    def load(self, name: str) -> Optional[Tool]:
        for tool in self.discover():
            if tool.name == name:
                return tool
        return None
