"""Placement tool using OpenROAD (Phase 3 Step 2).

Performs global and detailed placement of standard cells.
Returns an explicit error when OpenROAD is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, missing_input_result, missing_tool_result, trust_metadata


class PlacementTool(Tool):
    name = "run_placement"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        floorplan = ctx.inputs.get("floorplan", {})

        code = netlist or reg_code
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No netlist or reg_code provided"},
                issues=["Missing input: netlist or reg_code"],
            )
        if not _has_openroad_technology(ctx):
            return _missing_openroad_technology()

        if not which_tool("openroad"):
            probe = run_eda_command(["openroad", "-version"], timeout=30)
            if probe.returncode != 0:
                return missing_tool_result(
                    "openroad",
                    "OpenROAD is not installed and CHIPAGENT_OPENROAD_IMAGE is unavailable. Configure CHIPAGENT_OPENROAD_IMAGE for physical design tools.",
                    install_url="https://openroad.readthedocs.io/",
                )

        # Run actual OpenROAD placement
        return self._run_openroad(code, floorplan)

    def _run_openroad(self, code: str, floorplan: dict) -> ToolResult:
        """Run OpenROAD placement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            netlist_file = tmpdir / "netlist.v"
            netlist_file.write_text(code)

            # Write OpenROAD script
            openroad_script = f"""
read_verilog netlist.v
link_design top

# Global placement
global_placement -density 0.7

# Detailed placement
detailed_placement

# Report placement
report_placement
report_design_area
"""
            script_file = tmpdir / "placement.tcl"
            script_file.write_text(openroad_script)

            try:
                command = ["openroad", "-exit", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="tool",
                                tool="openroad",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["OpenROAD placement failed"],
                    )

                # Parse placement results
                placement = self._parse_openroad_output(result.stdout)

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="openroad",
                            tool_available=True,
                            command=result.command or command,
                        ),
                        "placed_cells": placement.get("placed_cells", 0),
                        "timing_slack": placement.get("timing_slack", 0.0),
                        "congestion": placement.get("congestion", 0.0),
                        "report": result.stdout,
                    },
                    issues=[],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="openroad", tool_available=True),
                    },
                    issues=[f"OpenROAD execution error: {e}"],
                )

    def _parse_openroad_output(self, output: str) -> dict:
        """Parse OpenROAD placement report."""
        placement = {"placed_cells": 0, "timing_slack": 0.0, "congestion": 0.0}

        # Placed cells
        m = re.search(r"(\d+)\s+cells placed", output)
        if m:
            placement["placed_cells"] = int(m.group(1))

        # Timing slack
        m = re.search(r"slack\s+([-\d.]+)", output)
        if m:
            placement["timing_slack"] = float(m.group(1))

        return placement


def _has_openroad_technology(ctx: ToolContext) -> bool:
    floorplan = ctx.inputs.get("floorplan") or {}
    return bool(
        ctx.inputs.get("tech_lef")
        and (ctx.inputs.get("liberty") or ctx.inputs.get("liberty_file"))
    ) or bool(
        floorplan.get("tech_lef")
        and (floorplan.get("liberty") or floorplan.get("liberty_file"))
    )


def _missing_openroad_technology() -> ToolResult:
    return missing_input_result(
        "OpenROAD placement requires technology inputs: tech_lef and liberty/liberty_file. "
        "The OpenROAD image may be installed, but placement cannot run without PDK data.",
        required_inputs=["tech_lef", "liberty or liberty_file"],
        tool="openroad",
    )
