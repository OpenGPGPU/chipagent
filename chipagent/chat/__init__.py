"""Deprecated conversational agent layer.

ChipAgent's maintained interactive API is the MCP server in :mod:`chipagent.mcp`.
The old in-process chat classes remain importable for compatibility but are no
longer wired into package entry points.
"""
from __future__ import annotations

__all__ = ["ChatSession", "repl"]


def __getattr__(name: str):
    if name == "ChatSession":
        from .session import ChatSession

        return ChatSession
    if name == "repl":
        from .cli import repl

        return repl
    raise AttributeError(name)
