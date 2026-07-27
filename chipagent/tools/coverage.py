"""Coverage analysis Tool (Phase 2 Step 4 / §3.2.3).

Parses a coverage report when one is supplied and, in the absence of a real
coverage database, returns a deterministic **stub** coverage derived from the
simulation pass/fail outcome. This is the "伪覆盖率先返回 stub" path the plan
explicitly allows for Phase 2 — the interface is real so a later phase can
swap in a true coverage database reader without changing callers.

Inputs (``ctx.inputs``):
  - ``sim_result``  : the dict produced by :class:`RunSimulationTool`
  - ``report_path`` : optional path to a coverage report file (UCDB / text)

Output: ``{"toggle": <pct>, "functional": <pct>, "status": ..., "source": ...}``
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


class AnalyzeCoverageTool(Tool):
    name = "analyze_coverage"

    def run(self, ctx: ToolContext) -> ToolResult:
        sim: Dict[str, Any] = ctx.inputs.get("sim_result") or {}
        report_path = ctx.inputs.get("report_path")

        # 1. If a real coverage report is provided, try to read a percentage.
        if report_path:
            parsed = self._parse_report(report_path)
            if parsed is not None:
                return ToolResult(result={
                    "toggle": parsed, "functional": parsed,
                    "status": "passed", "source": f"report:{Path(report_path).name}",
                })

        # 2. Stub coverage derived from the simulation outcome.
        passed = sim.get("passed", "skipped")
        if passed == "passed":
            toggle, functional = 80, 70
            status = "passed"
        elif passed == "failed":
            toggle, functional = 40, 30
            status = "failed"
        else:  # skipped / unknown
            toggle, functional = 0, 0
            status = "skipped"
        return ToolResult(
            result={
                "toggle": toggle, "functional": functional,
                "status": status, "source": "stub:from_sim_result",
            },
            issues=[] if status in ("passed", "skipped") else ["simulation failed; coverage unreliable"],
        )

    @staticmethod
    def _parse_report(report_path: str) -> float | None:
        path = Path(report_path)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        # Common patterns: "Toggle Coverage: 82.5%" or "82.5%" after a header.
        m = re.search(r"toggle[^\d]{0,20}(\d+(?:\.\d+)?)\s*%", text, re.I)
        if m:
            return float(m.group(1))
        m = re.search(r"coverage[^\d]{0,20}(\d+(?:\.\d+)?)\s*%", text, re.I)
        if m:
            return float(m.group(1))
        return None
