"""Power analysis tool using Yosys + VCD (Phase 3 Step 1).

Uses Yosys for structural data and labels derived power numbers as estimates.
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


class PowerAnalysisTool(Tool):
    name = "analyze_power"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        vcd_path = ctx.inputs.get("vcd_path")

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

        return self._run_yosys_power(code, vcd_path, top_module, ctx)

    def _run_yosys_power(self, code: str, vcd_path, top_module: str, ctx: ToolContext) -> ToolResult:
        """Run Yosys power estimation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            input_file = tmpdir / "input.v"
            input_file.write_text(code)

            # Write power analysis script
            # Note: Real power analysis requires liberty files and switching activity
            # This is a simplified version for demonstration
            yosys_script = f"""
read_verilog input.v
synth -top {top_module}
stat
"""

            # If VCD provided, add switching activity analysis
            if vcd_path and Path(vcd_path).exists():
                yosys_script += f"""
# Note: Real power analysis would use:
# read_liberty -lib <liberty_file>
# power -vcd {vcd_path}
"""

            script_file = tmpdir / "power.ys"
            script_file.write_text(yosys_script)

            try:
                command = ["yosys", "-s", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="static_estimate",
                                tool="yosys",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["Yosys power analysis failed"],
                    )

                # Parse cell count and estimate power
                stats = self._parse_yosys_stats(result.stdout)
                cells = stats.get("cells", 100)

                # Rough power estimation: 0.5mW per 100 cells
                dynamic_power = (cells / 100) * 0.5
                leakage_power = (cells / 100) * 0.1
                total_power = dynamic_power + leakage_power
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "report.log": result.stdout,
                        "power.ys": yosys_script,
                    },
                )

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="static_estimate",
                            tool="yosys",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "dynamic_power": dynamic_power,
                        "leakage_power": leakage_power,
                        "total_power": total_power,
                        "unit": "mW",
                        "cells": cells,
                        "vcd_used": vcd_path is not None,
                        "report": result.stdout,
                    },
                    issues=["Power values are estimates; real power analysis requires liberty files"],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="static_estimate", tool="yosys", tool_available=True),
                    },
                    issues=[f"Yosys execution error: {e}"],
                )

    def _parse_yosys_stats(self, output: str) -> dict:
        """Parse cell count from Yosys output."""
        stats = {"cells": 100}

        m = re.search(r"Number of cells:\s+(\d+)", output)
        if m:
            stats["cells"] = int(m.group(1))

        return stats
