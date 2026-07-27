"""Clock Tree Synthesis tool using OpenROAD (Phase 3 Step 2).

Synthesizes clock tree with target skew and insertion delay.
Returns an explicit error when OpenROAD is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, missing_input_result, missing_tool_result, trust_metadata


class CTSTool(Tool):
    name = "run_cts"

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()
        placement = ctx.inputs.get("placement", {})

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

        # Run actual OpenROAD CTS
        return self._run_openroad(code)

    def _run_openroad(self, code: str) -> ToolResult:
        """Run OpenROAD CTS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write netlist
            netlist_file = tmpdir / "netlist.v"
            netlist_file.write_text(code)

            # Write OpenROAD script
            openroad_script = f"""
read_verilog netlist.v
link_design top

# Clock tree synthesis
clock_tree_synthesis \\
  -root_buf CLKBUF_X1 \\
  -buf_list CLKBUF_X1 \\
  -wire_segment segment1

# Report CTS
report_cts
"""
            script_file = tmpdir / "cts.tcl"
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
                        issues=["OpenROAD CTS failed"],
                    )

                # Parse CTS results
                cts = self._parse_openroad_output(result.stdout)

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="openroad",
                            tool_available=True,
                            command=result.command or command,
                        ),
                        "skew": cts.get("skew", 0.0),
                        "insertion_delay": cts.get("insertion_delay", 0.0),
                        "buffer_count": cts.get("buffer_count", 0),
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
        """Parse OpenROAD CTS report."""
        cts = {"skew": 0.0, "insertion_delay": 0.0, "buffer_count": 0}

        # Skew
        m = re.search(r"skew[:\s]+([-\d.]+)", output, re.IGNORECASE)
        if m:
            cts["skew"] = float(m.group(1))

        # Insertion delay
        m = re.search(r"insertion[:\s]+([-\d.]+)", output, re.IGNORECASE)
        if m:
            cts["insertion_delay"] = float(m.group(1))

        # Buffer count
        m = re.search(r"(\d+)\s+buffers inserted", output)
        if m:
            cts["buffer_count"] = int(m.group(1))

        return cts


def _has_openroad_technology(ctx: ToolContext) -> bool:
    placement = ctx.inputs.get("placement") or {}
    return bool(
        ctx.inputs.get("tech_lef")
        and (ctx.inputs.get("liberty") or ctx.inputs.get("liberty_file"))
    ) or bool(
        placement.get("tech_lef")
        and (placement.get("liberty") or placement.get("liberty_file"))
    )


def _missing_openroad_technology() -> ToolResult:
    return missing_input_result(
        "OpenROAD CTS requires technology inputs: tech_lef and liberty/liberty_file. "
        "The OpenROAD image may be installed, but CTS cannot run without PDK data.",
        required_inputs=["tech_lef", "liberty or liberty_file"],
        tool="openroad",
    )
