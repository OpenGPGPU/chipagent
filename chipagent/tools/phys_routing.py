"""Routing tool using OpenROAD (Phase 3 Step 2).

Performs global and detailed routing of signal nets.
Returns an explicit error when OpenROAD is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, missing_input_result, missing_tool_result, trust_metadata


class RoutingTool(Tool):
    name = "run_routing"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        cts = ctx.inputs.get("cts", {})

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

        # Run actual OpenROAD routing
        return self._run_openroad(code)

    def _run_openroad(self, code: str) -> ToolResult:
        """Run OpenROAD routing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            netlist_file = tmpdir / "netlist.v"
            netlist_file.write_text(code)

            # Write OpenROAD script
            openroad_script = f"""
read_verilog netlist.v
link_design top

# Global routing
global_route \\
  -guide_file route_guide.def

# Detailed routing
detailed_route \\
  -output_drc route_drc.rpt

# Report routing
report_routing
"""
            script_file = tmpdir / "routing.tcl"
            script_file.write_text(openroad_script)

            try:
                command = ["openroad", "-exit", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=120)

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
                        issues=["OpenROAD routing failed"],
                    )

                # Parse routing results
                routing = self._parse_openroad_output(result.stdout)

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="openroad",
                            tool_available=True,
                            command=result.command or command,
                        ),
                        "wirelength": routing.get("wirelength", 0),
                        "drc_violations": routing.get("drc_violations", 0),
                        "congestion": routing.get("congestion", 0.0),
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
        """Parse OpenROAD routing report."""
        routing = {"wirelength": 0, "drc_violations": 0, "congestion": 0.0}

        # Wirelength
        m = re.search(r"Total wirelength[:\s]+(\d+)", output)
        if m:
            routing["wirelength"] = int(m.group(1))

        # DRC violations
        m = re.search(r"(\d+)\s+DRC violations", output)
        if m:
            routing["drc_violations"] = int(m.group(1))

        # Congestion
        m = re.search(r"congestion[:\s]+([\d.]+)", output, re.IGNORECASE)
        if m:
            routing["congestion"] = float(m.group(1))

        return routing


def _has_openroad_technology(ctx: ToolContext) -> bool:
    cts = ctx.inputs.get("cts") or {}
    return bool(
        ctx.inputs.get("tech_lef")
        and (ctx.inputs.get("liberty") or ctx.inputs.get("liberty_file"))
    ) or bool(
        cts.get("tech_lef")
        and (cts.get("liberty") or cts.get("liberty_file"))
    )


def _missing_openroad_technology() -> ToolResult:
    return missing_input_result(
        "OpenROAD routing requires technology inputs: tech_lef and liberty/liberty_file. "
        "The OpenROAD image may be installed, but routing cannot run without PDK data.",
        required_inputs=["tech_lef", "liberty or liberty_file"],
        tool="openroad",
    )
