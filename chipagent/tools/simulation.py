"""Run simulation Tool (Phase 2 Step 4).

Compiles an RTL + testbench pair with iverilog (preferred) or verilator and
runs the simulation, capturing pass/fail and the VCD path. Degrades gracefully
to ``passed="skipped"`` when no simulator is available in PATH or known local
tool environments.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from chipagent.toolchain import which_tool

from .base import Tool, ToolContext, ToolResult, persist_tool_artifacts, trust_metadata

def _which(name: str) -> Optional[str]:
    return which_tool(name)


class RunSimulationTool(Tool):
    name = "run_simulation"

    def run(self, ctx: ToolContext) -> ToolResult:
        reg_code = (ctx.inputs.get("reg_code") or "").strip()
        tb_code = (ctx.inputs.get("tb_code") or "").strip()
        if not reg_code or not tb_code:
            return ToolResult(
                result={"status": "skipped", "passed": "skipped"},
                issues=["missing reg_code or tb_code input"],
            )

        work = Path(ctx.work_dir or tempfile.mkdtemp(prefix="chipagent-sim-"))
        work.mkdir(parents=True, exist_ok=True)
        reg_path = work / "dut.sv"
        tb_path = work / "tb.sv"
        reg_path.write_text(reg_code, encoding="utf-8")
        tb_path.write_text(tb_code, encoding="utf-8")

        iverilog = _which("iverilog")
        vvp = _which("vvp")
        verilator = _which("verilator")

        if iverilog and vvp:
            return self._run_iverilog(iverilog, vvp, reg_path, tb_path, work, ctx)
        if verilator:
            return self._run_verilator(verilator, reg_path, tb_path, work, ctx)
        return ToolResult(
            result={"status": "skipped", "passed": "skipped", "reason": "no iverilog/verilator on PATH"},
            issues=[],
        )

    # ------------------------------------------------------------------
    def _run_iverilog(
        self,
        iverilog: str,
        vvp: str,
        reg: Path,
        tb: Path,
        work: Path,
        ctx: ToolContext,
    ) -> ToolResult:
        out = work / "sim.vvp"
        vcd = work / "sim.vcd"
        comp = subprocess.run(
            [iverilog, "-g2012", "-o", str(out), str(reg), str(tb)],
            capture_output=True, text=True, timeout=60,
        )
        if comp.returncode != 0:
            return ToolResult(
                result={"status": "failed", "passed": "failed", "stage": "compile", "tool": "iverilog"},
                issues=[f"compile error: {comp.stderr.strip()[:500]}"],
            )
        run = subprocess.run(
            [vvp, str(out)], capture_output=True, text=True, timeout=60,
            cwd=str(work),
        )
        passed = self._judge(run.stdout)
        files = {
            "stdout.log": run.stdout,
            "stderr.log": run.stderr,
            "compile_stderr.log": comp.stderr,
        }
        if vcd.exists():
            files["sim.vcd"] = vcd.read_text(encoding="utf-8", errors="replace")
        artifacts = persist_tool_artifacts(ctx, self.name, files)
        return ToolResult(
            result={
                "status": passed,
                "passed": passed,
                "tool": "iverilog",
                **trust_metadata(source="tool", tool="iverilog", tool_available=True),
                "stdout": run.stdout[:2000],
                "stderr": run.stderr[:500],
                "vcd_path": str(vcd) if vcd.exists() else "",
                "artifacts": artifacts,
            },
            issues=[] if passed == "passed" else [f"simulation did not report PASS"],
        )

    def _run_verilator(self, verilator: str, reg: Path, tb: Path, work: Path, ctx: ToolContext) -> ToolResult:
        # Verilator: lint+compile to a binary. Top module = tb.
        top = self._top_module(tb.read_text(encoding="utf-8")) or "tb"
        out = work / "sim_bin"
        comp = subprocess.run(
            [verilator, "--binary", "--top-module", top, "-Wno-fatal",
             str(reg), str(tb), "-o", str(out.name)],
            capture_output=True, text=True, timeout=120, cwd=str(work),
        )
        if comp.returncode != 0:
            return ToolResult(
                result={"status": "failed", "passed": "failed", "stage": "compile", "tool": "verilator"},
                issues=[f"compile error: {comp.stderr.strip()[:500]}"],
            )
        bin_path = work / "obj_dir" / out.name
        if not bin_path.exists():
            bin_path = out
        run = subprocess.run([str(bin_path)], capture_output=True, text=True, timeout=60, cwd=str(work))
        passed = self._judge(run.stdout)
        artifacts = persist_tool_artifacts(ctx, self.name, {
            "stdout.log": run.stdout,
            "stderr.log": run.stderr,
            "compile_stderr.log": comp.stderr,
        })
        return ToolResult(
            result={"status": passed, "passed": passed, "tool": "verilator",
                    **trust_metadata(source="tool", tool="verilator", tool_available=True),
                    "stdout": run.stdout[:2000], "stderr": run.stderr[:500],
                    "artifacts": artifacts},
            issues=[] if passed == "passed" else ["simulation did not report PASS"],
        )

    @staticmethod
    def _top_module(tb_code: str) -> Optional[str]:
        m = re.search(r"\bmodule\s+(\w+)", tb_code)
        return m.group(1) if m else None

    @staticmethod
    def _judge(stdout: str) -> str:
        # Convention: the testbench prints "PASS" on success.
        if re.search(r"\bPASS\b", stdout):
            return "passed"
        if re.search(r"\bFAIL\b", stdout):
            return "failed"
        return "failed"
