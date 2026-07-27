"""Area optimization tool using Yosys (Phase 3 Step 1).

Applies area optimization strategies: opt_clean, opt_merge, opt_expr.
Returns an explicit error when Yosys is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    missing_tool_result,
    persist_tool_artifacts,
    trust_metadata,
)


class AreaOptimizeTool(Tool):
    name = "optimize_area"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        area_constraint = ctx.inputs.get("area_constraint", None)

        code = netlist or reg_code
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No netlist or reg_code provided"},
                issues=["Missing input: netlist or reg_code"],
            )

        if not which_tool("yosys"):
            probe = run_eda_command(["yosys", "-V"], timeout=30)
            if probe.returncode != 0:
                message = probe.stderr.strip() or "Yosys not available on host or Docker tool image."
                if probe.mode == "unavailable":
                    message = "Yosys not installed and Docker tool image is unavailable. Run: bash scripts/setup_eda_env.sh --docker"
                return missing_tool_result("yosys", message, install_url="https://yosyshq.net/yosys/")

        top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
            return ToolResult(
                result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                issues=["Invalid input: module_name"],
            )

        return self._run_yosys_opt(code, area_constraint, top_module, ctx)

    def _run_yosys_opt(self, code: str, constraint, top_module: str, ctx: ToolContext) -> ToolResult:
        """Run Yosys area optimization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write input
            input_file = tmpdir / "input.v"
            input_file.write_text(code)

            # First, get baseline stats
            baseline_script = f"""
read_verilog input.v
synth -top {top_module}
stat
"""
            baseline_file = tmpdir / "baseline.ys"
            baseline_file.write_text(baseline_script)

            try:
                baseline_command = ["yosys", "-s", baseline_file.name]
                baseline_result = run_eda_command(baseline_command, work_dir=str(tmpdir), timeout=60)

                baseline_stats = self._parse_yosys_stats(baseline_result.stdout)
                baseline_cells = baseline_stats.get("cells", 0)

                # Now run optimization
                yosys_script = f"""
read_verilog input.v
synth -top {top_module}
opt_clean
opt_merge
opt_expr
opt_clean
stat
write_verilog -noattr optimized.v
"""
                script_file = tmpdir / "opt.ys"
                script_file.write_text(yosys_script)

                command = ["yosys", "-s", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="tool",
                                tool="yosys",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["Yosys optimization failed"],
                    )

                # Read optimized netlist
                opt_file = tmpdir / "optimized.v"
                optimized_code = opt_file.read_text() if opt_file.exists() else code
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "optimized.v": optimized_code,
                        "report.log": result.stdout,
                        "opt.ys": yosys_script,
                        "baseline.ys": baseline_script,
                    },
                ) or {"optimized_netlist": str(opt_file)}

                # Parse optimized cell count
                opt_stats = self._parse_yosys_stats(result.stdout)
                opt_cells = opt_stats.get("cells", 0)

                # Calculate reduction
                reduction_percent = ((baseline_cells - opt_cells) / baseline_cells * 100) if baseline_cells > 0 else 0

                # Check constraint if provided
                issues = []
                if constraint and opt_cells > constraint:
                    issues.append(f"Cell count {opt_cells} exceeds constraint {constraint}")

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="yosys",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "optimized_netlist": optimized_code,
                        "area_before": baseline_cells,
                        "area_after": opt_cells,
                        "cells_before": baseline_cells,
                        "cells_after": opt_cells,
                        "reduction_percent": reduction_percent,
                        "report": result.stdout,
                    },
                    issues=issues,
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="yosys", tool_available=True),
                    },
                    issues=[f"Yosys execution error: {e}"],
                )

    def _parse_yosys_stats(self, output: str) -> dict:
        """Parse cell count from Yosys output."""
        stats = {"cells": 0}

        m = re.search(r"Number of cells:\s+(\d+)", output)
        if m:
            stats["cells"] = int(m.group(1))

        return stats
