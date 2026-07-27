"""Synthesis tool using Yosys (Phase 3 Step 1).

Runs full RTL synthesis: read_verilog → synth → stat → write_verilog.
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


class SynthesisTool(Tool):
    name = "run_synthesis"

    def run(self, ctx: ToolContext) -> ToolResult:
        reg_code = ctx.inputs.get("reg_code", "").strip()
        if not reg_code:
            return ToolResult(
                result={"status": "error", "message": "No reg_code provided"},
                issues=["Missing input: reg_code"],
            )

        # Check if Yosys is available on the host or via the Docker tool image.
        if not which_tool("yosys"):
            probe = run_eda_command(["yosys", "-V"], timeout=30)
            if probe.returncode != 0:
                message = probe.stderr.strip() or "Yosys not available on host or Docker tool image."
                if probe.mode == "unavailable":
                    message = "Yosys not installed and Docker tool image is unavailable. Run: bash scripts/setup_eda_env.sh --docker"
                return missing_tool_result(
                    "yosys",
                    message,
                    install_url="https://yosyshq.net/yosys/",
                )

        top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
            return ToolResult(
                result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                issues=["Invalid input: module_name"],
            )
        return self._run_yosys(reg_code, ctx, top_module)

    def _run_yosys(self, reg_code: str, ctx: ToolContext, top_module: str) -> ToolResult:
        """Run actual Yosys synthesis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write input Verilog
            input_file = tmpdir / "input.v"
            input_file.write_text(reg_code)

            # Write Yosys script
            yosys_script = f"""
read_verilog input.v
synth -top {top_module}
stat
write_verilog -noattr netlist.v
"""
            script_file = tmpdir / "synth.ys"
            script_file.write_text(yosys_script)

            # Run Yosys
            try:
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
                        issues=["Yosys synthesis failed"],
                    )

                # Parse statistics from output
                stats = self._parse_yosys_stats(result.stdout)

                # Read netlist
                netlist_file = tmpdir / "netlist.v"
                netlist = netlist_file.read_text() if netlist_file.exists() else ""
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "netlist.v": netlist,
                        "report.log": result.stdout,
                        "synth.ys": yosys_script,
                    },
                ) or {"netlist": str(netlist_file)}

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
                        "netlist": netlist,
                        "report": result.stdout,
                        "cells": stats.get("cells", 0),
                        "area": stats.get("area", 0.0),
                        "wires": stats.get("wires", 0),
                        "public_wires": stats.get("public_wires", 0),
                    },
                    issues=[],
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
        """Parse cell count and area from Yosys stat output."""
        stats = {"cells": 0, "area": 0.0, "wires": 0, "public_wires": 0}

        # Number of cells: 123
        m = re.search(r"Number of cells:\s+(\d+)", output)
        if m:
            stats["cells"] = int(m.group(1))

        # Number of wires: 456
        m = re.search(r"Number of wires:\s+(\d+)", output)
        if m:
            stats["wires"] = int(m.group(1))

        # Number of public wires: 78
        m = re.search(r"Number of public wires:\s+(\d+)", output)
        if m:
            stats["public_wires"] = int(m.group(1))

        # Chip area (if available)
        m = re.search(r"Chip area for (?:top module|module) '[^']+':\s+([\d.]+)", output)
        if m:
            stats["area"] = float(m.group(1))

        return stats
