"""Floorplan tool using OpenROAD (Phase 3 Step 2).

Creates initial floorplan with die area, core area, and placement constraints.
Returns an explicit error when OpenROAD is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, missing_input_result, missing_tool_result, trust_metadata


class FloorplanTool(Tool):
    name = "create_floorplan"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        constraints = ctx.inputs.get("constraints", {})

        code = netlist or reg_code
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No netlist or reg_code provided"},
                issues=["Missing input: netlist or reg_code"],
            )

        # Extract constraints
        target_util = constraints.get("utilization", 0.7)
        aspect_ratio = constraints.get("aspect_ratio", 1.0)
        if not _has_openroad_technology(ctx):
            return _missing_openroad_technology()

        if not which_tool("openroad"):
            probe = run_eda_command(["openroad", "-version"], timeout=30)
            if probe.returncode != 0:
                return missing_tool_result(
                    "openroad",
                    "OpenROAD is not installed and CHIPAGENT_OPENROAD_IMAGE is unavailable. Configure CHIPAGENT_OPENROAD_IMAGE for physical design tools.",
                    install_url="https://openroad.readthedocs.io/en/latest/user/BuildLocally.html",
                )

        return self._run_openroad(code, target_util, aspect_ratio)

    def _run_openroad(self, code: str, target_util: float, aspect_ratio: float) -> ToolResult:
        """Run OpenROAD floorplan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            netlist_file = tmpdir / "netlist.v"
            netlist_file.write_text(code)

            # Write OpenROAD script
            # Note: Real floorplan requires LEF/DEF files and technology library
            # This is a simplified version for demonstration
            openroad_script = f"""
read_verilog netlist.v
link_design top

# Initialize floorplan with target utilization and aspect ratio
initialize_floorplan \\
  -utilization {target_util} \\
  -aspect_ratio {aspect_ratio} \\
  -core_space 10

# Report floorplan
report_design_area
"""
            script_file = tmpdir / "floorplan.tcl"
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
                        issues=["OpenROAD floorplan failed"],
                    )

                # Parse floorplan results
                fp = self._parse_openroad_output(result.stdout)

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="openroad",
                            tool_available=True,
                            command=result.command or command,
                        ),
                        "die_area": fp.get("die_area", 0),
                        "core_area": fp.get("core_area", 0),
                        "utilization": fp.get("utilization", target_util),
                        "aspect_ratio": aspect_ratio,
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
        """Parse OpenROAD floorplan report."""
        fp = {"die_area": 0, "core_area": 0, "utilization": 0.0}

        # Design area
        m = re.search(r"Design area\s+(\d+)\s+u\^2", output)
        if m:
            fp["core_area"] = int(m.group(1))

        # Utilization
        m = re.search(r"(\d+(?:\.\d+)?)%\s+utilization", output)
        if m:
            fp["utilization"] = float(m.group(1)) / 100.0

        return fp


def _has_openroad_technology(ctx: ToolContext) -> bool:
    constraints = ctx.inputs.get("constraints") or {}
    return bool(
        ctx.inputs.get("tech_lef")
        and (ctx.inputs.get("liberty") or ctx.inputs.get("liberty_file"))
    ) or bool(
        constraints.get("tech_lef")
        and (constraints.get("liberty") or constraints.get("liberty_file"))
    )


def _missing_openroad_technology() -> ToolResult:
    return missing_input_result(
        "OpenROAD physical design requires technology inputs: tech_lef and liberty/liberty_file. "
        "The OpenROAD image may be installed, but floorplanning cannot run without PDK data.",
        required_inputs=["tech_lef", "liberty or liberty_file"],
        tool="openroad",
    )
