"""Tool framework (Phase 2 Step 1 / MCP capability layer).

A Tool is the Python-backed analog of a text Skill: it wraps an external
command or check (verilator/iverilog simulation, register alignment, etc.).
Tools live under ``chipagent/tools/`` and are discovered by :class:`ToolLoader`
via importlib — adding a Tool means dropping a ``<name>.py`` with a ``Tool``
subclass, no registration code. This mirrors the text-Skill discovery model
in :mod:`chipagent.skills.loader`.

The MCP protocol transport layer (stdio/SSE) is a future Phase 2 increment;
Phase 2 first cut runs tools in-process via :meth:`Tool.run`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..models import TaskObject


@dataclass
class ToolContext:
    """Inputs handed to a Tool at execution time.

    ``inputs`` carries upstream artifacts from prior workflow steps (e.g.
    ``{"reg_code": "...", "header_code": "..."}``) so a Tool can operate on
    the chain's intermediate products.
    """

    task: TaskObject
    inputs: Dict[str, Any] = field(default_factory=dict)
    settings: Optional[Settings] = None
    work_dir: Optional[str] = None  # scratch dir for compile artifacts


@dataclass
class ToolResult:
    """Standardised Tool output."""

    result: Dict[str, Any] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def normalized(
        self,
        *,
        tool_name: Optional[str] = None,
        default_source: str = "structural_check",
    ) -> Dict[str, Any]:
        """Return a trust-contract-compatible result without dropping old fields.

        Older tools were written before the result trust contract was formalized,
        so some of them omit ``status``, ``source``, ``artifacts`` or ``issues``.
        This method provides a single compatibility layer for MCP callers while
        keeping each tool's original payload intact.
        """
        data = dict(self.result)
        issues = list(self.issues or data.get("issues") or [])

        data.setdefault("status", "failed" if issues else "passed")
        data.setdefault("source", default_source)
        data.setdefault("artifacts", {})
        data.setdefault("issues", issues)

        if tool_name and "tool" not in data:
            data["tool"] = tool_name

        if "tool_available" not in data and data.get("tool"):
            source = str(data.get("source") or "")
            data["tool_available"] = not (
                source.startswith("stub")
                or source == "template"
                or data.get("status") == "unavailable"
            )

        data.setdefault("command", None)
        return data


def trust_metadata(
    *,
    source: str,
    tool: Optional[str] = None,
    tool_available: Optional[bool] = None,
    command: Optional[Any] = None,
    artifacts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Common result metadata that tells callers how much to trust a result."""
    data: Dict[str, Any] = {
        "source": source,
        "artifacts": artifacts or {},
    }
    if tool is not None:
        data["tool"] = tool
    if tool_available is not None:
        data["tool_available"] = tool_available
    if command is not None:
        data["command"] = command
    return data


def missing_tool_result(tool: str, message: str, *, install_url: Optional[str] = None) -> ToolResult:
    """Return an honest unavailable/error result when a required backend is absent."""
    result = {
        "status": "error",
        "message": message,
        "required_tool": tool,
        **trust_metadata(source="tool", tool=tool, tool_available=False),
    }
    if install_url:
        result["install_url"] = install_url
    return ToolResult(result=result, issues=[f"{tool} not available"])


def missing_input_result(
    message: str,
    *,
    required_inputs: List[str],
    tool: Optional[str] = None,
) -> ToolResult:
    """Return an honest error when a backend exists but required design inputs do not."""
    return ToolResult(
        result={
            "status": "error",
            "message": message,
            "required_inputs": required_inputs,
            **trust_metadata(source="tool", tool=tool, tool_available=True),
        },
        issues=[message],
    )


def persist_tool_artifacts(
    ctx: ToolContext,
    tool_name: str,
    files: Dict[str, str],
) -> Dict[str, str]:
    """Persist text artifacts for an EDA tool when an output directory is supplied."""
    output_dir = ctx.inputs.get("output_dir") or None
    if not output_dir:
        return {}

    module = ctx.task.module_name or "module"
    safe_module = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in module)
    safe_tool = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in tool_name)
    out = Path(output_dir) / "eda" / safe_tool
    out.mkdir(parents=True, exist_ok=True)

    persisted: Dict[str, str] = {}
    for name, content in files.items():
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        path = out / f"{safe_module}_{safe_name}"
        path.write_text(content, encoding="utf-8")
        persisted[name] = str(path)
    return persisted


class Tool:
    """Base Tool interface. Subclasses set ``name`` and implement :meth:`run`."""

    name: str = "tool"

    def run(self, ctx: ToolContext) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError
