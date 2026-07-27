"""Timing analysis tool using OpenSTA (Phase 3 Step 1).

Performs static timing analysis on synthesized netlists.
Uses OpenSTA for real timing; Yosys-only results are explicitly marked as estimates.
"""
import re
import subprocess
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, persist_tool_artifacts, trust_metadata


class TimingAnalysisTool(Tool):
    name = "analyze_timing"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()

        code = netlist or reg_code
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No netlist or reg_code provided"},
                issues=["Missing input: netlist or reg_code"],
            )

        sta_available = which_tool("sta") or run_eda_command(["sta", "-version"], timeout=30).returncode == 0
        yosys_available = which_tool("yosys") or run_eda_command(["yosys", "-V"], timeout=30).returncode == 0
        liberty = (ctx.inputs.get("liberty") or "").strip()
        liberty_file = (ctx.inputs.get("liberty_file") or "").strip()

        # OpenSTA needs a timing library. Without one, return a clearly marked
        # structural estimate rather than a misleading failed STA run.
        if sta_available and (liberty or liberty_file):
            sta_result = self._run_opensta(code, ctx)
            if sta_result.result.get("status") == "success" or not yosys_available:
                return sta_result
            fallback = self._run_yosys_stat(code, ctx)
            fallback.issues.insert(0, "OpenSTA failed; fell back to Yosys structural estimate")
            return fallback

        # Try yosys stat for basic info
        if yosys_available:
            return self._run_yosys_stat(code, ctx)

        # No tools available - return error
        return ToolResult(
            result={
                "status": "error",
                "message": "No timing analysis tools available. Please install OpenSTA (https://github.com/The-OpenROAD-Project/OpenSTA) or Yosys (https://yosyshq.net/yosys/)",
                "required_tools": ["sta", "yosys"],
                **trust_metadata(source="tool", tool_available=False),
            },
            issues=["OpenSTA and Yosys not available"],
        )

    def _run_opensta(self, code: str, ctx: ToolContext) -> ToolResult:
        """Run OpenSTA timing analysis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            netlist_file = tmpdir / "netlist.v"
            netlist_file.write_text(code)

            liberty = (ctx.inputs.get("liberty") or "").strip()
            liberty_file = (ctx.inputs.get("liberty_file") or "").strip()
            if liberty:
                library_file = tmpdir / "library.lib"
                library_file.write_text(liberty)
            else:
                source_liberty = Path(liberty_file)
                if not source_liberty.exists():
                    return ToolResult(
                        result={
                            "status": "error",
                            "message": f"Liberty file not found: {liberty_file}",
                            **trust_metadata(source="tool", tool="opensta", tool_available=True),
                        },
                        issues=[f"Liberty file not found: {liberty_file}"],
                    )
                library_file = tmpdir / "library.lib"
                library_file.write_text(source_liberty.read_text())

            # Write SDC (default: 100MHz clock)
            sdc_file = tmpdir / "timing.sdc"
            sdc_file.write_text((ctx.inputs.get("sdc") or """
create_clock -name clk -period 10.0 [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks clk]
""").strip() + "\n")

            # Write STA script
            sta_script = f"""
read_liberty library.lib
read_verilog netlist.v
link_design top
read_sdc timing.sdc
report_checks -path_delay max
report_tns
report_wns
"""
            script_file = tmpdir / "timing.sta"
            script_file.write_text(sta_script)

            try:
                command = ["sta", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="tool",
                                tool="opensta",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["OpenSTA analysis failed"],
                    )

                # Parse timing results
                timing = self._parse_sta_output(result.stdout)
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "report.log": result.stdout,
                        "timing.sta": sta_script,
                    },
                )

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="opensta",
                            tool_available=True,
                            command=command,
                            artifacts=artifacts,
                        ),
                        "slack": timing.get("slack", 0.0),
                        "wns": timing.get("wns", 0.0),
                        "tns": timing.get("tns", 0.0),
                        "critical_path": timing.get("critical_path", ""),
                        "report": result.stdout,
                    },
                    issues=[],
                )

            except subprocess.TimeoutExpired:
                return ToolResult(
                    result={
                        "status": "timeout",
                        **trust_metadata(source="tool", tool="opensta", tool_available=True),
                    },
                    issues=["OpenSTA analysis timed out"],
                )
            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="opensta", tool_available=True),
                    },
                    issues=[f"OpenSTA execution error: {e}"],
                )

    def _run_yosys_stat(self, code: str, ctx: ToolContext) -> ToolResult:
        """Use yosys stat for basic structural estimation, not real STA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            input_file = tmpdir / "input.v"
            input_file.write_text(code)

            top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
                return ToolResult(
                    result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                    issues=["Invalid input: module_name"],
                )

            yosys_script = f"""
read_verilog input.v
synth -top {top_module}
stat
"""
            script_file = tmpdir / "stat.ys"
            script_file.write_text(yosys_script)

            try:
                command = ["yosys", "-s", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                cells = 0
                m = re.search(r"Number of cells:\s+(\d+)", result.stdout)
                if m:
                    cells = int(m.group(1))

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
                        issues=["Yosys stat failed"],
                    )

                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "report.log": result.stdout,
                        "stat.ys": yosys_script,
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
                        "slack": None,
                        "wns": None,
                        "tns": None,
                        "critical_path": "",
                        "cell_count": cells,
                        "report": result.stdout,
                    },
                    issues=["OpenSTA not available; timing result is a Yosys structural estimate"],
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

    def _parse_sta_output(self, output: str) -> dict:
        """Parse OpenSTA report output."""
        timing = {"slack": 0.0, "wns": 0.0, "tns": 0.0, "critical_path": ""}

        # slack (required time - arrival time)
        m = re.search(r"slack\s+\(VIOLATED\)\s+([-\d.]+)", output)
        if m:
            timing["slack"] = float(m.group(1))

        # wns (worst negative slack)
        m = re.search(r"wns\s+([-\d.]+)", output)
        if m:
            timing["wns"] = float(m.group(1))

        # tns (total negative slack)
        m = re.search(r"tns\s+([-\d.]+)", output)
        if m:
            timing["tns"] = float(m.group(1))

        # critical path (first path mentioned)
        m = re.search(r"Path:\s+([^\n]+)", output)
        if m:
            timing["critical_path"] = m.group(1).strip()

        return timing
