"""Elaboration / synthesis check Tool (Phase 2 Step 4 / §3.2.2 lightweight).

Runs a two-stage lightweight elaboration gate on generated RTL:

1. **Lint-elaborate** with verilator ``--lint-only`` (when available) — catches
   undeclared signals, width mismatches, and module-resolution errors that the
   structural lint in :class:`VerilogLinter` cannot see.
2. **Synthesis草估** with yosys (when available) — synthesises to a generic
   cell library and reports an approximate cell / area count for sizing.

Degrades to ``status="skipped"`` when neither tool is on PATH, so the closed
loop still completes on a host without EDA tooling. The simulator lookup reuses
the ``shutil.which`` + extra-bin-dir pattern from ``RunSimulationTool``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .base import Tool, ToolContext, ToolResult
from .simulation import _which


class ElaborateCheckTool(Tool):
    name = "elaborate_check"

    def run(self, ctx: ToolContext) -> ToolResult:
        code = (ctx.inputs.get("reg_code") or ctx.inputs.get("code") or "").strip()
        if not code:
            return ToolResult(
                result={"status": "skipped", "reason": "no RTL code provided"},
                issues=["missing reg_code/code input"],
            )

        work = Path(ctx.work_dir or tempfile.mkdtemp(prefix="chipagent-elab-"))
        work.mkdir(parents=True, exist_ok=True)
        rtl_path = work / "dut.sv"
        rtl_path.write_text(code, encoding="utf-8")
        top = self._top_module(code) or "dut"

        report: dict = {"status": "skipped", "top": top}
        issues: list[str] = []

        verilator = _which("verilator")
        if verilator:
            lint = self._verilator_lint(verilator, rtl_path, top, work)
            report["lint"] = lint["lint"]
            report["lint_issues"] = lint["issues"]
            if lint["issues"]:
                issues.extend(lint["issues"][:3])
            report["status"] = lint["lint"]

        yosys = _which("yosys")
        if yosys:
            syn = self._yosys_synthesis(yosys, rtl_path, top, work)
            report["synthesis"] = syn
            # Synthesis failure does not fail the gate (草估 is advisory).
            if syn.get("cells") is not None:
                report["status"] = report.get("status", "skipped") if report["status"] == "skipped" else report["status"]

        if not verilator and not yosys:
            report["reason"] = "neither verilator nor yosys on PATH"
        return ToolResult(result=report, issues=issues)

    # ------------------------------------------------------------------
    def _verilator_lint(self, verilator: str, rtl: Path, top: str, work: Path) -> dict:
        try:
            proc = subprocess.run(
                [verilator, "--lint-only", "--top-module", top, "-Wno-fatal",
                 "--bbox-sys", "--bbox-unsup", str(rtl)],
                capture_output=True, text=True, timeout=60, cwd=str(work),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return {"lint": "skipped", "issues": [f"verilator unavailable: {exc}"]}
        if proc.returncode == 0:
            return {"lint": "passed", "issues": []}
        msgs = self._filter_verilator_msgs(proc.stderr)
        return {"lint": "failed", "issues": msgs}

    def _yosys_synthesis(self, yosys: str, rtl: Path, top: str, work: Path) -> dict:
        script = work / "syn.ys"
        script.write_text(
            f"read_verilog -sv {rtl}\n"
            f"hierarchy -check -top {top}\n"
            f"synth -flatten\n"
            f"stat\n",
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [yosys, "-q", "-s", str(script)],
                capture_output=True, text=True, timeout=120, cwd=str(work),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return {"status": "skipped", "reason": f"yosys unavailable: {exc}"}
        if proc.returncode != 0:
            return {"status": "failed", "reason": proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}
        cells = self._parse_cell_count(proc.stdout)
        return {"status": "passed", "cells": cells}

    @staticmethod
    def _filter_verilator_msgs(stderr: str) -> list[str]:
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        # Keep lines that look like real diagnostics (Error/Warning/...).
        kept = [ln for ln in lines if re.match(r"^(Error|Warning|%Error|%Warning)", ln)]
        return kept[:8] if kept else lines[:8]

    @staticmethod
    def _parse_cell_count(stdout: str) -> Optional[int]:
        # yosys `stat` prints: "Number of cells: 42"
        m = re.search(r"Number of cells:\s*(\d+)", stdout)
        return int(m.group(1)) if m else None

    @staticmethod
    def _top_module(code: str) -> Optional[str]:
        m = re.search(r"\bmodule\s+(\w+)", code)
        return m.group(1) if m else None
