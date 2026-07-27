"""Layout vs Schematic tool using Netgen (Phase 3 Step 2).

Compares layout extracted netlist with schematic netlist for consistency.
Returns an explicit error when Netgen is unavailable.
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


class LVSCheckTool(Tool):
    name = "run_lvs_check"

    def run(self, ctx: ToolContext) -> ToolResult:
        layout = ctx.inputs.get("layout", "").strip()
        netlist = ctx.inputs.get("netlist", "").strip()

        if not layout or not netlist:
            return ToolResult(
                result={"status": "error", "message": "Missing layout or netlist"},
                issues=["Missing input: layout or netlist"],
            )

        if not which_tool("netgen-lvs"):
            probe = run_eda_command(["netgen-lvs", "-batch", "version"], timeout=30)
            if probe.returncode != 0:
                detail = (probe.stderr or probe.stdout or "").strip()
                message = "Netgen LVS not available on host or Docker tool image."
                if detail:
                    message = f"{message} Probe output: {detail[:300]}"
                if probe.mode == "unavailable":
                    message = "Netgen LVS is not installed and Docker tool image is unavailable. Run: bash scripts/setup_eda_env.sh --docker"
                return missing_tool_result("netgen-lvs", message, install_url="http://opencircuitdesign.com/netgen/")

        return self._run_netgen(layout, netlist, ctx)

    def _run_netgen(self, layout: str, netlist: str, ctx: ToolContext) -> ToolResult:
        """Run Netgen LVS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write layout netlist
            layout_file = tmpdir / "layout.spice"
            layout_file.write_text(layout)

            # Write schematic netlist
            schematic_file = tmpdir / "schematic.spice"
            schematic_file.write_text(netlist)

            # Write Netgen script
            netgen_script = f"""
lvs {{ layout.spice top }} {{ schematic.spice top }} lvs_report.txt
quit
"""
            script_file = tmpdir / "lvs.tcl"
            script_file.write_text(netgen_script)

            try:
                command = ["netgen-lvs", "-batch", "source", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                # Read LVS report
                report_file = tmpdir / "lvs_report.txt"
                report = report_file.read_text() if report_file.exists() else result.stdout

                # Parse LVS results
                lvs = self._parse_netgen_output(report)

                match = lvs.get("match", False)
                mismatches = lvs.get("mismatches", [])
                artifacts = persist_tool_artifacts(ctx, self.name, {
                    "layout.spice": layout,
                    "schematic.spice": netlist,
                    "lvs.tcl": netgen_script,
                    "lvs_report.txt": report,
                }) or {"lvs_report": str(report_file)}

                return ToolResult(
                    result={
                        "status": "success" if match else "failed",
                        **trust_metadata(
                            source="tool",
                            tool="netgen-lvs",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "match": match,
                        "mismatches": mismatches,
                        "devices_compared": lvs.get("devices_compared", 0),
                        "report": report,
                    },
                    issues=[] if match else ["LVS mismatch detected"],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="netgen", tool_available=True),
                    },
                    issues=[f"Netgen execution error: {e}"],
                )

    def _parse_netgen_output(self, output: str) -> dict:
        """Parse Netgen LVS report."""
        lvs = {"match": False, "mismatches": [], "devices_compared": 0}

        # Check for match
        if "match uniquely" in output or "match correctly" in output:
            lvs["match"] = True

        # Parse device count
        m = re.search(r"Total devices:\s*(\d+)", output)
        if m:
            lvs["devices_compared"] = int(m.group(1))

        # Parse mismatches
        for m in re.finditer(r"(\w+)\s+mismatch", output):
            lvs["mismatches"].append({
                "type": m.group(1),
                "description": f"{m.group(1)} mismatch",
            })

        return lvs
