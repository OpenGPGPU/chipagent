"""Tool framework (Phase 2 capability layer / MCP).

Tools are Python wrappers around external commands or checks (simulation,
alignment, ...), discovered by :class:`ToolLoader`. Adding a Tool means
dropping a ``<name>.py`` under this package — no registration code.
"""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult
from .loader import ToolLoader

__all__ = ["Tool", "ToolContext", "ToolResult", "ToolLoader"]
